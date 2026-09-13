import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_RESUME_COMMAND = "fcc-claude --continue -p"
DEFAULT_CLAUDE_NEW_SESSION_COMMAND = ""
DEFAULT_CLAUDE_NEW_SESSION_PROMPT_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_NEW_SESSION_NOTICE = (
    "Context phien chat gan nhat da dung {percent:.1f}%, Niko mo phien chat moi nhe."
)
DEFAULT_CONTEXT_LIMIT_PERCENT = 80.0
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
GROUP_MODE_MENTIONS = "mentions"
GROUP_MODE_ALL = "all"
SESSION_MODE_STATELESS = "stateless"
SESSION_MODE_AUTO_RESUME = "auto_resume"
DEFAULT_TELEGRAM_PROMPT_HOOK = (
    "Ban la Niko, mot AI Agent dang chat chit voi mot dam duc rua trong group Telegram. "
    "Noi chuyen bang tieng Viet, than mat, lanh loi, hai huoc het co the. "
    "Phong cach nhu anh em trong ban nhau: vui, nhanh, co ca khia nhe, nhung khong cong kich ca nhan qua da. "
    "Tra loi gon, dung chat group, tranh van phong mau me. "
    "Bat buoc ket thuc moi cau tra loi bang dung cum: Ok nhé bạn"
)
DEFAULT_TELEGRAM_REPLY_SUFFIX = "Ok nhé bạn"


class TelegramError(RuntimeError):
    pass


@dataclass
class ClaudeSessionPlan:
    prompt_command: str
    notice: str = ""


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


def build_telegram_prompt(user_prompt: str) -> str:
    hook = os.getenv("TELEGRAM_PROMPT_HOOK", DEFAULT_TELEGRAM_PROMPT_HOOK).strip()
    if not hook:
        return user_prompt

    return f"{hook}\n\nTin nhan nguoi dung:\n{user_prompt}"


def ensure_reply_suffix(answer: str) -> str:
    suffix = os.getenv("TELEGRAM_REPLY_SUFFIX", DEFAULT_TELEGRAM_REPLY_SUFFIX).strip()
    answer = answer.strip()
    if not suffix or answer.endswith(suffix):
        return answer

    return f"{answer}\n\n{suffix}"


def call_claude(prompt: str, command: str | None = None) -> str:
    command = command or os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)
    return run_cli(command, prompt) or "(Khong co noi dung tra ve.)"


def parse_context_usage_percent(output: str) -> float | None:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        data = None

    json_percent = find_context_percent(data)
    if json_percent is not None:
        return json_percent

    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", output)
    if not match:
        return None

    return float(match.group(1).replace(",", "."))


def find_context_percent(value) -> float | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if "percent" in key_lower or "percentage" in key_lower:
                percent = coerce_percent(child)
                if percent is not None:
                    return percent

            percent = find_context_percent(child)
            if percent is not None:
                return percent

    if isinstance(value, list):
        for child in value:
            percent = find_context_percent(child)
            if percent is not None:
                return percent

    return None


def coerce_percent(value) -> float | None:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None

    if 0 <= percent <= 1:
        return percent * 100
    if 0 <= percent <= 100:
        return percent
    return None


def get_context_usage_percent() -> float | None:
    command = os.getenv("CLAUDE_CONTEXT_USAGE_COMMAND", "").strip()
    if not command:
        return None

    timeout_seconds = int(os.getenv("CLAUDE_CONTROL_TIMEOUT_SECONDS", "30"))
    output = run_cli(command, timeout_seconds=timeout_seconds)
    percent = parse_context_usage_percent(output)
    if percent is None:
        raise RuntimeError(f"Khong doc duoc % context tu output cua: {command}")
    return percent


def build_new_session_notice(percent: float) -> str:
    template = os.getenv("CLAUDE_NEW_SESSION_NOTICE", DEFAULT_CLAUDE_NEW_SESSION_NOTICE)
    return template.format(percent=percent)


def prepare_new_claude_session() -> None:
    command = os.getenv("CLAUDE_NEW_SESSION_COMMAND", DEFAULT_CLAUDE_NEW_SESSION_COMMAND).strip()
    if not command:
        return

    timeout_seconds = int(os.getenv("CLAUDE_CONTROL_TIMEOUT_SECONDS", "30"))
    run_cli(command, timeout_seconds=timeout_seconds)


def plan_claude_session() -> ClaudeSessionPlan:
    session_mode = os.getenv("CLAUDE_SESSION_MODE", SESSION_MODE_STATELESS).strip().lower()
    if session_mode != SESSION_MODE_AUTO_RESUME:
        return ClaudeSessionPlan(os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND))

    percent = get_context_usage_percent()
    if percent is None:
        return ClaudeSessionPlan(os.getenv("CLAUDE_RESUME_COMMAND", DEFAULT_CLAUDE_RESUME_COMMAND))

    limit = float(os.getenv("CLAUDE_CONTEXT_LIMIT_PERCENT", str(DEFAULT_CONTEXT_LIMIT_PERCENT)))
    if percent < limit:
        return ClaudeSessionPlan(os.getenv("CLAUDE_RESUME_COMMAND", DEFAULT_CLAUDE_RESUME_COMMAND))

    prepare_new_claude_session()
    return ClaudeSessionPlan(
        os.getenv("CLAUDE_NEW_SESSION_PROMPT_COMMAND", DEFAULT_CLAUDE_NEW_SESSION_PROMPT_COMMAND),
        build_new_session_notice(percent),
    )


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


def handle_message(token: str, message: dict, allowed_chat_ids: set[int], bot_username: str) -> None:
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    print(f"Nhan tin nhan tu chat_id={chat_id}")

    if is_command_for_bot(text, "/id", bot_username):
        send_message(token, chat_id, f"chat_id cua chat nay la: {chat_id}")
        return

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        print(f"Bo qua chat_id chua duoc phep: {chat_id}")
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
        send_chat_action(token, chat_id)
        session_plan = plan_claude_session()
        if session_plan.notice:
            send_message(token, chat_id, session_plan.notice)
        answer = ensure_reply_suffix(call_claude(build_telegram_prompt(prompt), session_plan.prompt_command))
        send_message(token, chat_id, answer)
    except Exception as exc:
        send_message(token, chat_id, f"Loi: {exc}")


def main() -> int:
    load_env_file()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Thieu TELEGRAM_BOT_TOKEN. Hay tao file .env hoac set bien moi truong.", file=sys.stderr)
        return 1

    allowed_chat_ids = parse_allowed_chat_ids()
    offset = None

    bot_username = get_bot_username(token)
    print("Telegram Claude bot dang chay. Nhan Ctrl+C de dung.")
    print(f"Bot username: @{bot_username}")
    if not allowed_chat_ids:
        print("Canh bao: TELEGRAM_ALLOWED_CHAT_IDS dang trong, bot se tra loi moi chat gui den.")

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
                    handle_message(token, message, allowed_chat_ids, bot_username)
        except KeyboardInterrupt:
            print("\nDa dung bot.")
            return 0
        except Exception as exc:
            print(f"Loi vong lap bot: {exc}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
