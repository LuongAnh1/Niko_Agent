import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from bots.chat_gateway import (
        build_identity_context,
        format_identity_reply,
        parse_allowed_user_keys,
        parse_user_aliases,
        telegram_message_to_gateway,
    )
    from bots.sticker_picker import choose_sticker_file_id, load_sticker_config
    from bots.agent_router import (
        ROUTE_BUSY_REPLY,
        ROUTE_DELAYED_DEEP_AGENT,
        ROUTE_FAST_AGENT,
        ROUTE_LOCAL_REPLY,
        decide_agent_route,
    )
except ImportError:
    from chat_gateway import (
        build_identity_context,
        format_identity_reply,
        parse_allowed_user_keys,
        parse_user_aliases,
        telegram_message_to_gateway,
    )
    from sticker_picker import choose_sticker_file_id, load_sticker_config
    from agent_router import (
        ROUTE_BUSY_REPLY,
        ROUTE_DELAYED_DEEP_AGENT,
        ROUTE_FAST_AGENT,
        ROUTE_LOCAL_REPLY,
        decide_agent_route,
    )


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_DEEP_AGENT_COMMAND = ""
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
GROUP_MODE_MENTIONS = "mentions"
GROUP_MODE_ALL = "all"
DEFAULT_TELEGRAM_PROMPT_HOOK_FILE = "HOOK.md"
DEFAULT_TELEGRAM_REPLY_SUFFIX = "Meow"
DEFAULT_TELEGRAM_STICKER_CONFIG_FILE = "stickers/ducks.json"
TRUE_VALUES = {"1", "true", "yes", "on"}
AGENT_MODE_SINGLE = "single"
AGENT_MODE_TWO_AGENT = "two_agent"
DEFAULT_TELEGRAM_DEEP_WAIT_REPLY = (
    "Dạ anh đợi em chút, câu này cần phân tích kỹ hơn nên em đẩy sang Opus 5 rồi báo lại anh ngay."
)
DEFAULT_TELEGRAM_DEEP_BUSY_REPLY = (
    "Dạ anh đợi em chút, em vẫn đang xử lý câu trước. Anh cứ nhắn tiếp, khi có kết quả em sẽ gửi lại."
)
DEFAULT_TELEGRAM_UNCERTAIN_DELAY_SECONDS = 3.0
STICKER_SET_CACHE: dict[str, list[dict]] = {}


class TelegramError(RuntimeError):
    pass


@dataclass
class DeepAgentJob:
    chat_id: int
    user_key: str
    prompt: str
    started_at: float


DEEP_JOBS: dict[int, DeepAgentJob] = {}
DEEP_JOBS_LOCK = threading.Lock()
DEEP_AGENT_LOCK = threading.Lock()


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=os.name != "nt")


def build_cli_args(command: str, prompt: str | None = None) -> list[str]:
    args = split_command(os.path.expandvars(command))
    if args:
        args[0] = shutil.which(args[0]) or args[0]

    if prompt is None:
        return args

    if any("{prompt}" in arg for arg in args):
        return [arg.replace("{prompt}", prompt) for arg in args]

    return [*args, prompt]


def run_cli(command: str, prompt: str | None = None, timeout_seconds: int | None = None) -> str:
    if timeout_seconds is None:
        timeout_seconds = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "180"))

    try:
        result = subprocess.run(
            build_cli_args(command, prompt),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Khong tim thay lenh: {command}. Kiem tra PATH/env.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Lenh chay qua lau, da het timeout: {command}") from exc

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "CLI tra ve loi khong ro."
        raise RuntimeError(message)

    return result.stdout.strip()


def env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE_VALUES

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_project_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return repo_root() / path


def load_telegram_prompt_hook() -> str:
    hook_file = os.getenv("TELEGRAM_PROMPT_HOOK_FILE", DEFAULT_TELEGRAM_PROMPT_HOOK_FILE).strip()
    if not hook_file:
        return ""

    path = resolve_project_path(hook_file)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Khong tim thay file hook: {path}") from exc


def build_telegram_prompt(
    user_prompt: str,
    gateway_message=None,
    include_prompt_hook: bool = True,
) -> str:
    hook = ""
    if include_prompt_hook:
        hook = load_telegram_prompt_hook()

    identity_context = ""
    if gateway_message is not None and env_flag("CHAT_IDENTITY_ENABLED", "1"):
        identity_context = build_identity_context(gateway_message)

    parts = []
    if hook:
        parts.append(hook)
    if identity_context:
        parts.append(identity_context)
    if not parts:
        return user_prompt

    parts.append(f"Tin nhan nguoi dung:\n{user_prompt}")
    return "\n\n".join(parts)


def ensure_reply_suffix(answer: str) -> str:
    suffix = os.getenv("TELEGRAM_REPLY_SUFFIX", DEFAULT_TELEGRAM_REPLY_SUFFIX).strip()
    answer = answer.strip()
    if not suffix or answer.endswith(suffix):
        return answer

    return f"{answer}\n\n{suffix}"


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


def two_agent_mode_enabled() -> bool:
    mode = os.getenv("TELEGRAM_AGENT_MODE", AGENT_MODE_SINGLE).strip().lower()
    return mode in {AGENT_MODE_TWO_AGENT, "dual", "2", "true", "on"}


def fast_agent_command() -> str:
    return os.getenv("TELEGRAM_FAST_AGENT_COMMAND", "").strip()


def get_active_deep_job(chat_id: int) -> DeepAgentJob | None:
    with DEEP_JOBS_LOCK:
        return DEEP_JOBS.get(chat_id)


def has_active_deep_job(chat_id: int) -> bool:
    return get_active_deep_job(chat_id) is not None


def build_deep_wait_reply() -> str:
    template = os.getenv("TELEGRAM_DEEP_WAIT_REPLY", DEFAULT_TELEGRAM_DEEP_WAIT_REPLY)
    return template.format()


def build_deep_busy_reply(chat_id: int) -> str:
    job = get_active_deep_job(chat_id)
    elapsed_seconds = int(time.time() - job.started_at) if job else 0
    elapsed_minutes = max(0, elapsed_seconds // 60)
    template = os.getenv("TELEGRAM_DEEP_BUSY_REPLY", DEFAULT_TELEGRAM_DEEP_BUSY_REPLY)
    return template.format(elapsed_seconds=elapsed_seconds, elapsed_minutes=elapsed_minutes)


def uncertain_delay_seconds() -> float:
    raw_value = os.getenv(
        "TELEGRAM_UNCERTAIN_DELAY_SECONDS",
        str(DEFAULT_TELEGRAM_UNCERTAIN_DELAY_SECONDS),
    ).strip()
    try:
        return max(0.0, min(30.0, float(raw_value)))
    except ValueError:
        return DEFAULT_TELEGRAM_UNCERTAIN_DELAY_SECONDS


def delay_before_deep_agent_if_needed(route_kind: str) -> None:
    if route_kind != ROUTE_DELAYED_DEEP_AGENT:
        return
    delay_seconds = uncertain_delay_seconds()
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def call_fast_agent(prompt: str, gateway_message) -> str:
    command = fast_agent_command()
    if not command:
        raise RuntimeError("Chua cau hinh TELEGRAM_FAST_AGENT_COMMAND.")

    timeout_seconds = int(os.getenv("TELEGRAM_FAST_AGENT_TIMEOUT_SECONDS", "45"))
    fast_prompt = build_telegram_prompt(prompt, gateway_message, include_prompt_hook=True)
    return run_cli(command, fast_prompt, timeout_seconds=timeout_seconds) or "(Khong co noi dung tra ve.)"


def deep_agent_command() -> str:
    command = os.getenv("CLAUDE_DEEP_AGENT_COMMAND", DEFAULT_CLAUDE_DEEP_AGENT_COMMAND).strip()
    if command:
        return command
    return os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)


def call_deep_agent(prompt: str, gateway_message) -> str:
    deep_prompt = build_telegram_prompt(prompt, gateway_message, include_prompt_hook=True)
    return call_claude(deep_prompt, deep_agent_command())


def start_deep_agent_job(token: str, chat_id: int, prompt: str, prompt_message) -> bool:
    with DEEP_JOBS_LOCK:
        if chat_id in DEEP_JOBS:
            return False
        DEEP_JOBS[chat_id] = DeepAgentJob(chat_id, prompt_message.user.key, prompt, time.time())

    thread = threading.Thread(
        target=run_deep_agent_job,
        args=(token, chat_id, prompt, prompt_message),
        daemon=True,
    )
    thread.start()
    return True


def run_deep_agent_job(token: str, chat_id: int, prompt: str, prompt_message) -> None:
    try:
        with DEEP_AGENT_LOCK:
            send_chat_action(token, chat_id)
            answer = ensure_reply_suffix(call_deep_agent(prompt, prompt_message))

        send_message(token, chat_id, answer)
        maybe_send_sticker(token, chat_id, prompt, answer)
    except Exception as exc:
        send_message(token, chat_id, f"Loi deep agent: {exc}")
    finally:
        with DEEP_JOBS_LOCK:
            DEEP_JOBS.pop(chat_id, None)


def handle_two_agent_message(token: str, chat_id: int, prompt: str, prompt_message) -> None:
    route = decide_agent_route(
        prompt,
        deep_job_active=has_active_deep_job(chat_id),
        fast_agent_available=bool(fast_agent_command()),
    )
    print(f"Agent route: {route.kind} ({route.reason})")

    if route.kind == ROUTE_BUSY_REPLY:
        answer = ensure_reply_suffix(build_deep_busy_reply(chat_id))
        send_message(token, chat_id, answer)
        maybe_send_sticker(token, chat_id, prompt, answer)
        return

    if route.kind == ROUTE_LOCAL_REPLY:
        answer = ensure_reply_suffix(route.reply)
        send_message(token, chat_id, answer)
        maybe_send_sticker(token, chat_id, prompt, answer)
        return

    if route.kind == ROUTE_FAST_AGENT:
        try:
            send_chat_action(token, chat_id)
            answer = ensure_reply_suffix(call_fast_agent(prompt, prompt_message))
            send_message(token, chat_id, answer)
            maybe_send_sticker(token, chat_id, prompt, answer)
            return
        except Exception as exc:
            print(f"Fast agent loi, chuyen sang deep agent: {exc}", file=sys.stderr)

    delay_before_deep_agent_if_needed(route.kind)
    started = start_deep_agent_job(token, chat_id, prompt, prompt_message)
    answer = ensure_reply_suffix(build_deep_wait_reply() if started else build_deep_busy_reply(chat_id))
    send_message(token, chat_id, answer)
    maybe_send_sticker(token, chat_id, prompt, answer)


def call_claude(prompt: str, command: str | None = None) -> str:
    command = command or os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)
    return run_cli(command, prompt) or "(Khong co noi dung tra ve.)"


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


def send_message(token: str, chat_id: int, text: str) -> None:
    chunks = split_text(text, MAX_TELEGRAM_MESSAGE_LENGTH)
    for chunk in chunks:
        telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": chunk})


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

    if is_reply_to_bot(message, bot_username):
        return text

    if f"@{bot_username}".lower() in text.lower():
        return strip_bot_mentions(text, bot_username)

    return ""


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
    text = gateway_message.text

    print(f"Nhan tin nhan tu chat_id={chat_id}, user_key={gateway_message.user.key}")

    if is_command_for_bot(text, "/id", bot_username) or is_command_for_bot(text, "/whoami", bot_username):
        send_message(token, chat_id, format_identity_reply(gateway_message))
        return

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        print(f"Bo qua chat_id chua duoc phep: {chat_id}")
        return

    if allowed_user_keys and gateway_message.user.key not in allowed_user_keys:
        print(f"Bo qua user_key chua duoc phep: {gateway_message.user.key}")
        return

    if not text:
        send_message(token, chat_id, "Hien tai bot chi xu ly tin nhan text.")
        return

    if is_command_for_bot(text, "/start", bot_username):
        send_message(token, chat_id, "Gui tin nhan cho minh, minh se hoi Claude CLI va tra loi lai tai day.")
        return

    prompt = extract_group_prompt(message, bot_username)
    if not prompt:
        return

    try:
        prompt_message = gateway_message.with_text(prompt)
        if two_agent_mode_enabled():
            handle_two_agent_message(token, chat_id, prompt, prompt_message)
            return

        send_chat_action(token, chat_id)
        answer = ensure_reply_suffix(call_deep_agent(prompt, prompt_message))
        send_message(token, chat_id, answer)
        maybe_send_sticker(token, chat_id, prompt, answer)
    except Exception as exc:
        send_message(token, chat_id, f"Loi: {exc}")


def main() -> int:
    load_env_file()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Thieu TELEGRAM_BOT_TOKEN. Hay tao file .env hoac set bien moi truong.", file=sys.stderr)
        return 1

    allowed_chat_ids = parse_allowed_chat_ids()
    allowed_user_keys = parse_allowed_user_keys(os.getenv("CHAT_ALLOWED_USER_KEYS", ""))
    user_aliases = parse_user_aliases(os.getenv("CHAT_USER_ALIASES", ""))
    offset = None

    bot_username = get_bot_username(token)
    print("Telegram Claude bot dang chay. Nhan Ctrl+C de dung.")
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
