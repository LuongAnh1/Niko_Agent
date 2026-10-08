"""Telegram gateway cho Niko Agent.

File này cố tình chỉ làm những việc thuộc Telegram: long polling, auth theo
chat/user, lọc mention trong group, gửi reply/sticker và chuyển raw message
thành `ChatGatewayMessage`. Mọi quyết định agent, memory, triage hay Deep job
đều đi qua `GatewayRunner` để gateway không phình thành business logic.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from html import escape as html_escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bots.decision_model.sticker import (
    NO_STICKER_MOOD,
    StickerMoodDecision,
    available_moods_from_config,
    decide_sticker_mood,
)
from bots.telegram.instance_guard import TelegramBotAlreadyRunning, acquire_telegram_bot_instance
from bots.telegram.sticker_picker import choose_sticker_file_id, load_sticker_config
from niko.chat_gateway import (
    format_identity_reply,
    parse_allowed_user_keys,
    parse_user_aliases,
    telegram_message_to_gateway,
)
from niko.config import env_flag, env_value, load_env_files, resolve_project_path
from niko.gateway import GatewayRunner
from niko.harness.runtime_log import default_runtime_logger


MAX_TELEGRAM_MESSAGE_LENGTH = 4096
GROUP_MODE_MENTIONS = "mentions"
GROUP_MODE_ALL = "all"
DEFAULT_TELEGRAM_MENTION_REPLIES = "1"
DEFAULT_TELEGRAM_STICKER_CONFIG_FILE = "bots/telegram/stickers/ducks.json"
DEFAULT_TELEGRAM_REQUEST_TIMEOUT_SECONDS = 75
DEFAULT_TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS = 5
DEFAULT_TELEGRAM_STICKER_TIMEOUT_SECONDS = 5
DEFAULT_TELEGRAM_STARTUP_RETRIES = 2
DEFAULT_TELEGRAM_STARTUP_RETRY_DELAY_SECONDS = 3.0
STICKER_SET_CACHE: dict[str, list[dict]] = {}
GATEWAY_RUNNER = GatewayRunner()


def runtime_log(event: str, message: str, *, level: str = "info", data: dict | None = None) -> None:
    default_runtime_logger().event("telegram_bot", event, message, level=level, data=data or {})


class TelegramError(RuntimeError):
    """Lỗi Telegram API đã được đổi sang message nội bộ dễ log."""

    pass


def is_getupdates_conflict(error: Exception) -> bool:
    """Telegram 409 nghĩa là token này đang có long-polling instance khác."""
    message = str(error).lower()
    return "telegram http 409" in message and "getupdates" in message


def telegram_request_timeout_seconds() -> int:
    raw_value = env_value("TELEGRAM_REQUEST_TIMEOUT_SECONDS", str(DEFAULT_TELEGRAM_REQUEST_TIMEOUT_SECONDS)).strip()
    try:
        return max(5, int(raw_value))
    except ValueError:
        return DEFAULT_TELEGRAM_REQUEST_TIMEOUT_SECONDS


def telegram_chat_action_timeout_seconds() -> int:
    raw_value = env_value(
        "TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS",
        str(DEFAULT_TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS),
    ).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS


def telegram_sticker_timeout_seconds() -> int:
    raw_value = env_value(
        "TELEGRAM_STICKER_TIMEOUT_SECONDS",
        str(DEFAULT_TELEGRAM_STICKER_TIMEOUT_SECONDS),
    ).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_TELEGRAM_STICKER_TIMEOUT_SECONDS


def sticker_decision_model_enabled() -> bool:
    """Sticker không còn dùng keyword rule; tắt AI nghĩa là không gửi sticker."""
    return env_flag("TELEGRAM_STICKER_DECISION_MODEL_ENABLED", "1")


def telegram_startup_retries() -> int:
    raw_value = env_value("TELEGRAM_STARTUP_RETRIES", str(DEFAULT_TELEGRAM_STARTUP_RETRIES)).strip()
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_TELEGRAM_STARTUP_RETRIES


def telegram_startup_retry_delay_seconds() -> float:
    raw_value = env_value(
        "TELEGRAM_STARTUP_RETRY_DELAY_SECONDS",
        str(DEFAULT_TELEGRAM_STARTUP_RETRY_DELAY_SECONDS),
    ).strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return DEFAULT_TELEGRAM_STARTUP_RETRY_DELAY_SECONDS


def telegram_request(token: str, method: str, payload: dict, timeout_seconds: int | None = None) -> dict:
    """Gọi Telegram Bot API bằng stdlib để bot không cần dependency HTTP ngoài."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds or telegram_request_timeout_seconds()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TelegramError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except TimeoutError as exc:
        raise TelegramError(f"Telegram request timeout o method {method}.") from exc
    except URLError as exc:
        raise TelegramError(f"Loi ket noi Telegram: {exc.reason}") from exc

    if not data.get("ok"):
        raise TelegramError(data.get("description", "Telegram API error"))
    return data["result"]


def send_message(token: str, chat_id: int, text: str, parse_mode: str | None = None) -> None:
    """Gửi text, tự cắt theo giới hạn 4096 ký tự của Telegram."""
    chunks = split_text(text, MAX_TELEGRAM_MESSAGE_LENGTH)
    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        telegram_request(token, "sendMessage", payload)


def recipient_mention_label(gateway_message) -> str:
    user = gateway_message.user
    return user.display_name or user.mention or "anh"


def recipient_mention(gateway_message) -> tuple[str, str | None]:
    """Tính mention trong group; private chat không cần tag lại người gửi."""
    if gateway_message is None or not env_flag("TELEGRAM_MENTION_REPLIES", DEFAULT_TELEGRAM_MENTION_REPLIES):
        return "", None
    if gateway_message.chat_type not in {"group", "supergroup"}:
        return "", None

    if gateway_message.user.mention:
        return gateway_message.user.mention, None
    if gateway_message.user.user_id:
        user_id = html_escape(gateway_message.user.user_id, quote=True)
        label = html_escape(recipient_mention_label(gateway_message), quote=False)
        return f'<a href="tg://user?id={user_id}">{label}</a>', "HTML"

    return "", None


def format_reply_for_recipient(text: str, gateway_message) -> tuple[str, str | None]:
    """Gắn mention vào reply group mà vẫn escape HTML khi phải dùng tg://user."""
    mention, parse_mode = recipient_mention(gateway_message)
    if not mention:
        return text, None

    if parse_mode == "HTML":
        return f"{mention} {html_escape(text, quote=False)}", parse_mode

    if text.lstrip().lower().startswith(mention.lower()):
        return text, None
    return f"{mention} {text}", None


def send_reply(token: str, chat_id: int, text: str, gateway_message) -> None:
    reply_text, parse_mode = format_reply_for_recipient(text, gateway_message)
    send_message(token, chat_id, reply_text, parse_mode=parse_mode)


def send_chat_action(token: str, chat_id: int, action: str = "typing") -> None:
    telegram_request(
        token,
        "sendChatAction",
        {"chat_id": chat_id, "action": action},
        timeout_seconds=telegram_chat_action_timeout_seconds(),
    )


def split_text(text: str, max_length: int) -> list[str]:
    """Cắt thẳng theo độ dài; đủ cho baseline vì Telegram tự giữ thứ tự gửi."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = text
    while current:
        chunks.append(current[:max_length])
        current = current[max_length:]
    return chunks


def parse_allowed_chat_ids() -> set[int]:
    """Allowlist chat ở tầng Telegram; allowlist user nằm ở chat_gateway."""
    raw_value = env_value("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw_value:
        return set()

    allowed = set()
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            allowed.add(int(item))
    return allowed


def prepare_long_polling(token: str) -> None:
    """Gỡ webhook trước khi long polling để tránh Telegram giữ delivery cũ."""
    drop_pending = env_flag("TELEGRAM_DROP_PENDING_UPDATES", "0")
    max_retries = telegram_startup_retries()
    for attempt in range(max_retries + 1):
        try:
            telegram_request(token, "deleteWebhook", {"drop_pending_updates": drop_pending})
            return
        except TelegramError as exc:
            if attempt >= max_retries:
                runtime_log(
                    "delete_webhook_warning",
                    "Canh bao: khong goi duoc deleteWebhook luc khoi dong. "
                    f"Bot van tiep tuc long polling, loi gan nhat: {exc}",
                    level="warning",
                    data={"error": str(exc)},
                )
                return

            delay_seconds = telegram_startup_retry_delay_seconds()
            runtime_log(
                "delete_webhook_retry",
                "Canh bao: deleteWebhook bi loi, thu lai "
                f"{attempt + 1}/{max_retries} sau {delay_seconds:g}s: {exc}",
                level="warning",
                data={"attempt": attempt + 1, "max_retries": max_retries, "delay_seconds": delay_seconds},
            )
            if delay_seconds > 0:
                time.sleep(delay_seconds)


def get_bot_username(token: str) -> str:
    bot_info = telegram_request(token, "getMe", {})
    return str(bot_info.get("username", "")).strip()


def is_command_for_bot(text: str, command: str, bot_username: str) -> bool:
    """Nhận `/cmd` và `/cmd@BotName`, bỏ qua command gửi cho bot khác."""
    first_word = text.split(maxsplit=1)[0] if text else ""
    command_part, _, target = first_word.partition("@")
    return command_part == command and (not target or target.lower() == bot_username.lower())


def is_group_chat(message: dict) -> bool:
    return message.get("chat", {}).get("type") in {"group", "supergroup"}


def is_reply_to_bot(message: dict, bot_username: str) -> bool:
    reply = message.get("reply_to_message") or {}
    sender = reply.get("from") or {}
    return bool(sender.get("is_bot")) and str(sender.get("username", "")).lower() == bot_username.lower()


def strip_bot_mentions(text: str, bot_username: str) -> str:
    mention = f"@{bot_username}".lower()
    words = [word for word in text.split() if word.lower() != mention]
    return " ".join(words).strip()


def extract_group_prompt(message: dict, bot_username: str) -> str:
    """Trong group, chỉ trả text khi policy group cho phép bot xử lý."""
    text = (message.get("text") or "").strip()
    if not is_group_chat(message):
        return text

    group_mode = env_value("TELEGRAM_GROUP_MODE", GROUP_MODE_MENTIONS).strip().lower()
    if group_mode == GROUP_MODE_ALL:
        return strip_bot_mentions(text, bot_username)

    if f"@{bot_username}".lower() in text.lower():
        return strip_bot_mentions(text, bot_username)

    return ""


def load_effective_sticker_config() -> dict:
    """Nạp sticker config file rồi cho env override vài field hay đổi khi demo."""
    config_file = env_value("TELEGRAM_STICKER_CONFIG_FILE", DEFAULT_TELEGRAM_STICKER_CONFIG_FILE).strip()
    config = load_sticker_config(resolve_project_path(config_file))

    sticker_set_name = env_value("TELEGRAM_STICKER_SET_NAME", "").strip()
    if sticker_set_name:
        config["set_name"] = sticker_set_name

    sticker_mode = env_value("TELEGRAM_STICKER_MODE", "").strip()
    if sticker_mode:
        config["mode"] = sticker_mode

    return config


def get_sticker_set_stickers(token: str, set_name: str, timeout_seconds: int | None = None) -> list[dict]:
    """Cache sticker set trong process để mỗi reply không gọi getStickerSet lại."""
    if set_name not in STICKER_SET_CACHE:
        result = telegram_request(token, "getStickerSet", {"name": set_name}, timeout_seconds=timeout_seconds)
        STICKER_SET_CACHE[set_name] = result.get("stickers", [])
    return STICKER_SET_CACHE[set_name]


def send_sticker(token: str, chat_id: int, sticker_file_id: str, timeout_seconds: int | None = None) -> None:
    telegram_request(
        token,
        "sendSticker",
        {"chat_id": chat_id, "sticker": sticker_file_id},
        timeout_seconds=timeout_seconds,
    )


def format_probability_map(probabilities: dict[str, float]) -> str:
    items = [f"{key}={value:.3f}" for key, value in sorted(probabilities.items())]
    return "{" + ", ".join(items) + "}"


def format_sticker_decision_log(decision: StickerMoodDecision) -> str:
    """Dòng runtime log cho biết Nimble đã chọn mood sticker nào."""
    parts = [
        f"provider={decision.provider or 'ollama_nimble'}",
        f"mood={decision.mood}",
    ]
    if decision.model:
        parts.append(f"model={decision.model}")
    if decision.label:
        parts.append(f"label={decision.label}")
    if decision.confidence is not None:
        parts.append(f"confidence={decision.confidence:.3f}")
    if decision.probabilities:
        parts.append(f"probabilities={format_probability_map(decision.probabilities)}")
    return "Sticker decision: " + " ".join(parts)


def maybe_send_sticker(token: str, chat_id: int, user_prompt: str, answer: str) -> None:
    """Sticker là hiệu ứng phụ vui vẻ: lỗi sticker không được làm hỏng reply chính."""
    if not env_flag("TELEGRAM_STICKERS_ENABLED", "0"):
        return

    try:
        config = load_effective_sticker_config()
        set_name = str(config.get("set_name", "")).strip()
        if not set_name:
            return

        if not sticker_decision_model_enabled():
            runtime_log("sticker_decision_disabled", "Sticker decision: disabled")
            return

        decision = decide_sticker_mood(user_prompt, answer, available_moods_from_config(config))
        runtime_log(
            "sticker_decision",
            format_sticker_decision_log(decision),
            data={
                "provider": decision.provider or "ollama_nimble",
                "mood": decision.mood,
                "model": decision.model,
                "label": decision.label,
                "confidence": decision.confidence,
                "probabilities": decision.probabilities,
            },
        )
        if decision.mood == NO_STICKER_MOOD:
            return

        timeout_seconds = telegram_sticker_timeout_seconds()
        stickers = get_sticker_set_stickers(token, set_name, timeout_seconds=timeout_seconds)
        sticker_file_id = choose_sticker_file_id(stickers, config, decision.mood)
        if sticker_file_id:
            send_sticker(token, chat_id, sticker_file_id, timeout_seconds=timeout_seconds)
        else:
            runtime_log(
                "sticker_missing",
                f"Sticker decision: mood={decision.mood} nhung khong tim thay sticker phu hop.",
                level="warning",
                data={"mood": decision.mood},
            )
    except Exception as exc:
        runtime_log(
            "sticker_decision_failed",
            f"Sticker decision failed: {exc}",
            level="error",
            data={"error": str(exc)},
        )


def maybe_send_sticker_async(token: str, chat_id: int, user_prompt: str, answer: str) -> None:
    if not env_flag("TELEGRAM_STICKERS_ENABLED", "0"):
        return

    thread = threading.Thread(
        target=maybe_send_sticker,
        args=(token, chat_id, user_prompt, answer),
        daemon=True,
    )
    thread.start()


def decision_triage_status_line() -> str:
    """Tóm tắt provider triage đang bật để dashboard dễ kiểm tra cấu hình."""
    mode = env_value("NIKO_AGENT_MODE", "single", legacy_name="TELEGRAM_AGENT_MODE").strip() or "single"
    if env_flag("NIKO_DECISION_MODEL_ENABLED", "0"):
        model = env_value("NIKO_DECISION_MODEL_NAME", "nimble").strip() or "nimble"
        base_url = env_value("NIKO_DECISION_MODEL_BASE_URL", "http://localhost:11434").strip()
        keep_alive = env_value("NIKO_DECISION_MODEL_KEEP_ALIVE", "-1").strip() or "(default)"
        return (
            "Decision triage: Ollama local enabled "
            f"(mode={mode}, model={model}, base_url={base_url}, keep_alive={keep_alive})."
        )

    if env_value("NIKO_FAST_AGENT_COMMAND", "", legacy_name="TELEGRAM_FAST_AGENT_COMMAND").strip():
        return f"Decision triage: Ollama local disabled; using legacy Fast/Fable triage (mode={mode})."

    return f"Decision triage: disabled; uncertain prompts will hand off to Deep (mode={mode})."


def sticker_decision_status_line() -> str:
    """Tóm tắt cơ chế sticker để biết có gọi Nimble sau reply không."""
    if not env_flag("TELEGRAM_STICKERS_ENABLED", "0"):
        return "Sticker decision: disabled (TELEGRAM_STICKERS_ENABLED=0)."
    if sticker_decision_model_enabled():
        timeout = env_value("TELEGRAM_STICKER_DECISION_MODEL_TIMEOUT_SECONDS", "").strip() or "shared"
        return f"Sticker decision: Ollama local enabled (timeout={timeout})."
    return "Sticker decision: disabled; keyword rule fallback has been removed."


def deliver_niko_answer(token: str, chat_id: int, prompt: str, prompt_message, answer: str) -> None:
    """Một chỗ duy nhất cho reply cuối: text trước, sticker chạy nền sau."""
    send_reply(token, chat_id, answer, prompt_message)
    maybe_send_sticker_async(token, chat_id, prompt, answer)


def handle_message(
    token: str,
    message: dict,
    allowed_chat_ids: set[int],
    allowed_user_keys: set[str],
    user_aliases: dict[str, str],
    bot_username: str,
) -> None:
    """Xử lý một Telegram message đã lấy từ polling.

    Thứ tự này quan trọng: `/id` luôn hữu ích để cấu hình allowlist, group
    mention được lọc trước, rồi mới kiểm tra auth và đẩy sang GatewayRunner.
    """
    gateway_message = telegram_message_to_gateway(message, user_aliases)
    chat_id = message["chat"]["id"]
    raw_text = (message.get("text") or "").strip()

    runtime_log(
        "telegram_message_received",
        f"Nhan tin nhan tu chat_id={chat_id}, user_key={gateway_message.user.key}",
        data={"chat_id": chat_id, "user_key": gateway_message.user.key},
    )

    if is_command_for_bot(raw_text, "/id", bot_username) or is_command_for_bot(raw_text, "/whoami", bot_username):
        prompt_message = gateway_message.with_text(raw_text)
        send_reply(token, chat_id, format_identity_reply(prompt_message), prompt_message)
        runtime_log(
            "identity_command_handled",
            f"Da xu ly identity command cho chat_id={chat_id}",
            data={"chat_id": chat_id, "user_key": gateway_message.user.key},
        )
        return

    prompt = extract_group_prompt(message, bot_username)
    if not prompt:
        return

    prompt_message = gateway_message.with_text(prompt)

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        runtime_log(
            "telegram_chat_rejected",
            f"Bo qua chat_id chua duoc phep: {chat_id}",
            level="warning",
            data={"chat_id": chat_id},
        )
        return

    if allowed_user_keys and gateway_message.user.key not in allowed_user_keys:
        runtime_log(
            "telegram_user_rejected",
            f"Bo qua user_key chua duoc phep: {gateway_message.user.key}",
            level="warning",
            data={"user_key": gateway_message.user.key},
        )
        return

    if is_command_for_bot(prompt, "/start", bot_username):
        send_reply(token, chat_id, "Gui tin nhan cho minh, minh se hoi Niko Agent va tra loi lai tai day.", prompt_message)
        return

    def deliver_reply(answer: str) -> None:
        # Graph chỉ biết callback này, không biết Telegram token/API.
        deliver_niko_answer(token, chat_id, prompt, prompt_message, answer)

    def notify_working() -> None:
        # Typing indicator là tín hiệu UX, không nằm trong logic agent.
        send_chat_action(token, chat_id)

    try:
        route = GATEWAY_RUNNER.handle_message(prompt, prompt_message, deliver_reply, notify_working)
        runtime_log(
            "telegram_message_processed",
            f"Da xu ly Telegram message: route={route}, chat_id={chat_id}, user_key={gateway_message.user.key}",
            data={"route": route, "chat_id": chat_id, "user_key": gateway_message.user.key},
        )
    except Exception as exc:
        runtime_log(
            "telegram_message_error",
            f"Loi xu ly Telegram message: chat_id={chat_id}, user_key={gateway_message.user.key}, error={exc}",
            level="error",
            data={"chat_id": chat_id, "user_key": gateway_message.user.key, "error": str(exc)},
        )
        deliver_niko_answer(token, chat_id, prompt, prompt_message, f"Loi: {exc}")


def main() -> int:
    """Entrypoint long polling local cho Telegram bot."""
    load_env_files("telegram")

    token = env_value("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        runtime_log(
            "telegram_config_error",
            "Thieu TELEGRAM_BOT_TOKEN. Hay mo dashboard Config/Bots de nhap token.",
            level="error",
        )
        return 1

    allowed_chat_ids = parse_allowed_chat_ids()
    allowed_user_keys = parse_allowed_user_keys(env_value("CHAT_ALLOWED_USER_KEYS", ""))
    user_aliases = parse_user_aliases(env_value("CHAT_USER_ALIASES", ""))
    offset = None

    try:
        instance_lock = acquire_telegram_bot_instance()
    except TelegramBotAlreadyRunning as exc:
        runtime_log(
            "telegram_instance_conflict",
            str(exc),
            level="error",
            data=exc.info.to_dict(),
        )
        return 2
    runtime_log(
        "telegram_instance_lock_acquired",
        f"Da giu Telegram bot single-instance lock pid={instance_lock.info.pid}.",
        data=instance_lock.info.to_dict(),
    )

    bot_username = get_bot_username(token)
    decision_status = decision_triage_status_line()
    sticker_status = sticker_decision_status_line()
    runtime_log(
        "telegram_bot_started",
        f"Telegram Niko bot dang chay: @{bot_username}",
        data={
            "bot_username": bot_username,
            "decision_triage": decision_status,
            "sticker_decision": sticker_status,
            "allowed_chat_count": len(allowed_chat_ids),
            "allowed_user_count": len(allowed_user_keys),
        },
    )
    if not allowed_chat_ids:
        runtime_log(
            "telegram_allowlist_warning",
            "Canh bao: TELEGRAM_ALLOWED_CHAT_IDS dang trong, bot se tra loi moi chat gui den.",
            level="warning",
        )
    if allowed_user_keys:
        runtime_log(
            "telegram_user_allowlist_loaded",
            f"Chi tra loi {len(allowed_user_keys)} user_key duoc phep.",
            data={"allowed_user_count": len(allowed_user_keys)},
        )

    prepare_long_polling(token)

    while True:
        try:
            payload = {"timeout": 60, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset

            updates = telegram_request(token, "getUpdates", payload)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    handle_message(token, message, allowed_chat_ids, allowed_user_keys, user_aliases, bot_username)
        except KeyboardInterrupt:
            runtime_log("telegram_bot_stopped", "Da dung bot.")
            return 0
        except Exception as exc:
            if is_getupdates_conflict(exc):
                runtime_log(
                    "telegram_polling_conflict",
                    "Telegram getUpdates bi conflict: dang co mot bot instance khac dung cung token. "
                    "Hay dung process bot cu hoac dung dashboard Start cho duy nhat mot instance.",
                    level="error",
                    data={"error": str(exc)},
                )
                return 2
            runtime_log(
                "telegram_polling_error",
                f"Loi Telegram long polling: {exc}",
                level="error",
                data={"error": str(exc)},
            )
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
