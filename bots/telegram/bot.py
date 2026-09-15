from __future__ import annotations

import json
import os
import sys
import time
from html import escape as html_escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bots.telegram.sticker_picker import choose_sticker_file_id, load_sticker_config
from niko.graphs.chat_reply import ChatReplyGraph
from niko.chat_gateway import (
    format_identity_reply,
    parse_allowed_user_keys,
    parse_user_aliases,
    telegram_message_to_gateway,
)
from niko.config import env_flag, load_env_files, resolve_project_path


MAX_TELEGRAM_MESSAGE_LENGTH = 4096
GROUP_MODE_MENTIONS = "mentions"
GROUP_MODE_ALL = "all"
DEFAULT_TELEGRAM_MENTION_REPLIES = "1"
DEFAULT_TELEGRAM_STICKER_CONFIG_FILE = "bots/telegram/stickers/ducks.json"
STICKER_SET_CACHE: dict[str, list[dict]] = {}
CHAT_REPLY_GRAPH = ChatReplyGraph()


class TelegramError(RuntimeError):
    pass


def telegram_request(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=75) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TelegramError(f"Telegram HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise TelegramError(f"Loi ket noi Telegram: {exc.reason}") from exc

    if not data.get("ok"):
        raise TelegramError(data.get("description", "Telegram API error"))
    return data["result"]


def send_message(token: str, chat_id: int, text: str, parse_mode: str | None = None) -> None:
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
    telegram_request(token, "sendChatAction", {"chat_id": chat_id, "action": action})


def split_text(text: str, max_length: int) -> list[str]:
    if len(text) <= max_length:
        return [text]

    chunks = []
    current = text
    while current:
        chunks.append(current[:max_length])
        current = current[max_length:]
    return chunks


def parse_allowed_chat_ids() -> set[int]:
    raw_value = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw_value:
        return set()

    allowed = set()
    for item in raw_value.split(","):
        item = item.strip()
        if item:
            allowed.add(int(item))
    return allowed


def prepare_long_polling(token: str) -> None:
    drop_pending = os.getenv("TELEGRAM_DROP_PENDING_UPDATES", "0").strip() == "1"
    telegram_request(token, "deleteWebhook", {"drop_pending_updates": drop_pending})


def get_bot_username(token: str) -> str:
    bot_info = telegram_request(token, "getMe", {})
    return str(bot_info.get("username", "")).strip()


def is_command_for_bot(text: str, command: str, bot_username: str) -> bool:
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
    text = (message.get("text") or "").strip()
    if not is_group_chat(message):
        return text

    group_mode = os.getenv("TELEGRAM_GROUP_MODE", GROUP_MODE_MENTIONS).strip().lower()
    if group_mode == GROUP_MODE_ALL:
        return strip_bot_mentions(text, bot_username)

    if f"@{bot_username}".lower() in text.lower():
        return strip_bot_mentions(text, bot_username)

    return ""


def load_effective_sticker_config() -> dict:
    config_file = os.getenv("TELEGRAM_STICKER_CONFIG_FILE", DEFAULT_TELEGRAM_STICKER_CONFIG_FILE).strip()
    config = load_sticker_config(resolve_project_path(config_file))

    sticker_set_name = os.getenv("TELEGRAM_STICKER_SET_NAME", "").strip()
    if sticker_set_name:
        config["set_name"] = sticker_set_name

    sticker_mode = os.getenv("TELEGRAM_STICKER_MODE", "").strip()
    if sticker_mode:
        config["mode"] = sticker_mode

    return config


def get_sticker_set_stickers(token: str, set_name: str) -> list[dict]:
    if set_name not in STICKER_SET_CACHE:
        result = telegram_request(token, "getStickerSet", {"name": set_name})
        STICKER_SET_CACHE[set_name] = result.get("stickers", [])
    return STICKER_SET_CACHE[set_name]


def send_sticker(token: str, chat_id: int, sticker_file_id: str) -> None:
    telegram_request(token, "sendSticker", {"chat_id": chat_id, "sticker": sticker_file_id})


def maybe_send_sticker(token: str, chat_id: int, user_prompt: str, answer: str) -> None:
    if not env_flag("TELEGRAM_STICKERS_ENABLED", "0"):
        return

    try:
        config = load_effective_sticker_config()
        set_name = str(config.get("set_name", "")).strip()
        if not set_name:
            return

        stickers = get_sticker_set_stickers(token, set_name)
        sticker_file_id = choose_sticker_file_id(stickers, config, f"{user_prompt}\n{answer}")
        if sticker_file_id:
            send_sticker(token, chat_id, sticker_file_id)
    except Exception as exc:
        print(f"Khong gui duoc sticker Telegram: {exc}", file=sys.stderr)


def deliver_niko_answer(token: str, chat_id: int, prompt: str, prompt_message, answer: str) -> None:
    send_reply(token, chat_id, answer, prompt_message)
    maybe_send_sticker(token, chat_id, prompt, answer)


def handle_message(
    token: str,
    message: dict,
    allowed_chat_ids: set[int],
    allowed_user_keys: set[str],
    user_aliases: dict[str, str],
    bot_username: str,
) -> None:
    gateway_message = telegram_message_to_gateway(message, user_aliases)
    chat_id = message["chat"]["id"]
    raw_text = (message.get("text") or "").strip()

    print(f"Nhan tin nhan tu chat_id={chat_id}, user_key={gateway_message.user.key}")

    if is_command_for_bot(raw_text, "/id", bot_username) or is_command_for_bot(raw_text, "/whoami", bot_username):
        prompt_message = gateway_message.with_text(raw_text)
        send_reply(token, chat_id, format_identity_reply(prompt_message), prompt_message)
        return

    prompt = extract_group_prompt(message, bot_username)
    if not prompt:
        return

    prompt_message = gateway_message.with_text(prompt)

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        print(f"Bo qua chat_id chua duoc phep: {chat_id}")
        return

    if allowed_user_keys and gateway_message.user.key not in allowed_user_keys:
        print(f"Bo qua user_key chua duoc phep: {gateway_message.user.key}")
        return

    if is_command_for_bot(prompt, "/start", bot_username):
        send_reply(token, chat_id, "Gui tin nhan cho minh, minh se hoi Niko Agent va tra loi lai tai day.", prompt_message)
        return

    def deliver_reply(answer: str) -> None:
        deliver_niko_answer(token, chat_id, prompt, prompt_message, answer)

    def notify_working() -> None:
        send_chat_action(token, chat_id)

    try:
        CHAT_REPLY_GRAPH.handle_message(prompt, prompt_message, deliver_reply, notify_working)
    except Exception as exc:
        deliver_niko_answer(token, chat_id, prompt, prompt_message, f"Loi: {exc}")


def main() -> int:
    load_env_files("telegram")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Thieu TELEGRAM_BOT_TOKEN. Hay tao file bots/telegram/.env hoac set bien moi truong.", file=sys.stderr)
        return 1

    allowed_chat_ids = parse_allowed_chat_ids()
    allowed_user_keys = parse_allowed_user_keys(os.getenv("CHAT_ALLOWED_USER_KEYS", ""))
    user_aliases = parse_user_aliases(os.getenv("CHAT_USER_ALIASES", ""))
    offset = None

    bot_username = get_bot_username(token)
    print("Telegram Niko bot dang chay. Nhan Ctrl+C de dung.")
    print(f"Bot username: @{bot_username}")
    if not allowed_chat_ids:
        print("Canh bao: TELEGRAM_ALLOWED_CHAT_IDS dang trong, bot se tra loi moi chat gui den.")
    if allowed_user_keys:
        print(f"Chi tra loi {len(allowed_user_keys)} user_key duoc phep.")

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
            print("\nDa dung bot.")
            return 0
        except Exception as exc:
            print(f"Loi vong lap bot: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
