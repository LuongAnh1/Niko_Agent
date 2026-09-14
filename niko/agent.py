from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from niko.agent_router import (
    ROUTE_BUSY_REPLY,
    ROUTE_DELAYED_DEEP_AGENT,
    ROUTE_FAST_AGENT,
    ROUTE_LOCAL_REPLY,
    decide_agent_route,
)
from niko.chat_gateway import build_identity_context
from niko.config import env_flag, env_value, resolve_project_path


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_DEEP_AGENT_COMMAND = ""
DEFAULT_CLAUDE_WORKDIR = ""
DEFAULT_NIKO_PROMPT_HOOK_FILE = "niko/HOOK.md"
DEFAULT_NIKO_REPLY_SUFFIX = "Meow"
DEFAULT_TOOL_UNAVAILABLE_REPLY = (
    "DÃ¡ÂºÂ¡ hiÃ¡Â»â€¡n tÃ¡ÂºÂ¡i em khÃƒÂ´ng cÃƒÂ³ quyÃ¡Â»Ân tÃ¡Â»Â± Ã„â€˜Ã¡Â»Âc file hay quÃƒÂ©t thÃ†Â° mÃ¡Â»Â¥c. "
    "NÃ¡ÂºÂ¿u anh muÃ¡Â»â€˜n em xem file/tÃƒÂ i liÃ¡Â»â€¡u nÃƒÂ o, anh gÃ¡Â»Â­i nÃ¡Â»â„¢i dung hoÃ¡ÂºÂ·c Ã„â€˜Ã¡Â»Æ’ harness nÃ¡ÂºÂ¡p phÃ¡ÂºÂ§n liÃƒÂªn quan vÃƒÂ o prompt giÃƒÂºp em nhÃƒÂ©."
)
AGENT_MODE_SINGLE = "single"
AGENT_MODE_TWO_AGENT = "two_agent"
DEFAULT_NIKO_DEEP_WAIT_REPLY = (
    "DÃ¡ÂºÂ¡ anh Ã„â€˜Ã¡Â»Â£i em chÃƒÂºt, cÃƒÂ¢u nÃƒÂ y cÃ¡ÂºÂ§n phÃƒÂ¢n tÃƒÂ­ch kÃ¡Â»Â¹ hÃ†Â¡n nÃƒÂªn em Ã„â€˜Ã¡ÂºÂ©y sang Opus 5 rÃ¡Â»â€œi bÃƒÂ¡o lÃ¡ÂºÂ¡i anh ngay."
)
DEFAULT_NIKO_DEEP_BUSY_REPLY = (
    "DÃ¡ÂºÂ¡ anh Ã„â€˜Ã¡Â»Â£i em chÃƒÂºt, em vÃ¡ÂºÂ«n Ã„â€˜ang xÃ¡Â»Â­ lÃƒÂ½ cÃƒÂ¢u trÃ†Â°Ã¡Â»â€ºc. Anh cÃ¡Â»Â© nhÃ¡ÂºÂ¯n tiÃ¡ÂºÂ¿p, khi cÃƒÂ³ kÃ¡ÂºÂ¿t quÃ¡ÂºÂ£ em sÃ¡ÂºÂ½ gÃ¡Â»Â­i lÃ¡ÂºÂ¡i."
)
DEFAULT_NIKO_UNCERTAIN_DELAY_SECONDS = 3.0

ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


@dataclass
class DeepAgentJob:
    conversation_id: str
    user_key: str
    prompt: str
    started_at: float


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


def resolve_claude_workdir() -> Path | None:
    raw_path = os.getenv("CLAUDE_WORKDIR", DEFAULT_CLAUDE_WORKDIR).strip()
    if not raw_path:
        return None

    path = resolve_project_path(raw_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cli(command: str, prompt: str | None = None, timeout_seconds: int | None = None) -> str:
    if timeout_seconds is None:
        timeout_seconds = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "180"))

    try:
        result = subprocess.run(
            build_cli_args(command, prompt),
            cwd=resolve_claude_workdir(),
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


def load_prompt_hook() -> str:
    hook_file = env_value(
        "NIKO_PROMPT_HOOK_FILE",
        DEFAULT_NIKO_PROMPT_HOOK_FILE,
        legacy_name="TELEGRAM_PROMPT_HOOK_FILE",
    ).strip()
    if not hook_file:
        return ""

    path = resolve_project_path(hook_file)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Khong tim thay file hook: {path}") from exc


def build_niko_prompt(user_prompt: str, gateway_message=None, include_prompt_hook: bool = True) -> str:
    hook = ""
    if include_prompt_hook:
        hook = load_prompt_hook()

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


build_telegram_prompt = build_niko_prompt
load_telegram_prompt_hook = load_prompt_hook


def reply_suffix() -> str:
    return env_value("NIKO_REPLY_SUFFIX", DEFAULT_NIKO_REPLY_SUFFIX, legacy_name="TELEGRAM_REPLY_SUFFIX").strip()


def strip_existing_reply_suffix(answer: str) -> str:
    suffix = reply_suffix()
    stripped = answer.strip()
    if suffix and stripped.lower().endswith(suffix.lower()):
        return stripped[: -len(suffix)].strip()
    return stripped


def is_tool_call_dict(value) -> bool:
    if not isinstance(value, dict):
        return False

    tool_name = str(value.get("tool") or value.get("name") or "").strip().lower()
    if not tool_name:
        return False

    tool_payload_keys = {"arguments", "input", "path", "command"}
    if not any(key in value for key in tool_payload_keys):
        return False

    return True


def looks_like_fake_tool_call(answer: str) -> bool:
    text = strip_existing_reply_suffix(answer)
    lowered = text.lower()
    if not lowered:
        return False

    if lowered.startswith("<tool") or "</tool>" in lowered:
        return True

    json_text = text
    if "</tool>" in lowered:
        json_text = text[: lowered.find("</tool>")].strip()

    if json_text.startswith("```"):
        lines = json_text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            json_text = "\n".join(lines[1:-1]).strip()

    if not json_text.startswith("{"):
        return False

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return False

    return is_tool_call_dict(data)


def sanitize_tool_like_answer(answer: str) -> str:
    if looks_like_fake_tool_call(answer):
        return env_value(
            "NIKO_TOOL_UNAVAILABLE_REPLY",
            DEFAULT_TOOL_UNAVAILABLE_REPLY,
            legacy_name="TELEGRAM_TOOL_UNAVAILABLE_REPLY",
        ).strip()
    return answer


def ensure_reply_suffix(answer: str) -> str:
    suffix = reply_suffix()
    answer = sanitize_tool_like_answer(answer).strip()
    if not suffix or answer.endswith(suffix):
        return answer

    return f"{answer}\n\n{suffix}"


def two_agent_mode_enabled() -> bool:
    mode = env_value("NIKO_AGENT_MODE", AGENT_MODE_SINGLE, legacy_name="TELEGRAM_AGENT_MODE").strip().lower()
    return mode in {AGENT_MODE_TWO_AGENT, "dual", "2", "true", "on"}


def fast_agent_command() -> str:
    return env_value("NIKO_FAST_AGENT_COMMAND", "", legacy_name="TELEGRAM_FAST_AGENT_COMMAND").strip()


def fast_agent_timeout_seconds() -> int:
    return int(env_value("NIKO_FAST_AGENT_TIMEOUT_SECONDS", "45", legacy_name="TELEGRAM_FAST_AGENT_TIMEOUT_SECONDS"))


def build_deep_wait_reply() -> str:
    template = env_value(
        "NIKO_DEEP_WAIT_REPLY",
        DEFAULT_NIKO_DEEP_WAIT_REPLY,
        legacy_name="TELEGRAM_DEEP_WAIT_REPLY",
    )
    return template.format()


def build_deep_busy_reply(job: DeepAgentJob | None) -> str:
    elapsed_seconds = int(time.time() - job.started_at) if job else 0
    elapsed_minutes = max(0, elapsed_seconds // 60)
    template = env_value(
        "NIKO_DEEP_BUSY_REPLY",
        DEFAULT_NIKO_DEEP_BUSY_REPLY,
        legacy_name="TELEGRAM_DEEP_BUSY_REPLY",
    )
    return template.format(elapsed_seconds=elapsed_seconds, elapsed_minutes=elapsed_minutes)


def uncertain_delay_seconds() -> float:
    raw_value = env_value(
        "NIKO_UNCERTAIN_DELAY_SECONDS",
        str(DEFAULT_NIKO_UNCERTAIN_DELAY_SECONDS),
        legacy_name="TELEGRAM_UNCERTAIN_DELAY_SECONDS",
    ).strip()
    try:
        return max(0.0, min(30.0, float(raw_value)))
    except ValueError:
        return DEFAULT_NIKO_UNCERTAIN_DELAY_SECONDS


def delay_before_deep_agent_if_needed(route_kind: str) -> None:
    if route_kind != ROUTE_DELAYED_DEEP_AGENT:
        return
    delay_seconds = uncertain_delay_seconds()
    if delay_seconds > 0:
        time.sleep(delay_seconds)


def call_fast_agent(prompt: str, gateway_message) -> str:
    command = fast_agent_command()
    if not command:
        raise RuntimeError("Chua cau hinh NIKO_FAST_AGENT_COMMAND.")

    fast_prompt = build_niko_prompt(prompt, gateway_message, include_prompt_hook=True)
    return run_cli(command, fast_prompt, timeout_seconds=fast_agent_timeout_seconds()) or "(Khong co noi dung tra ve.)"


def deep_agent_command() -> str:
    command = os.getenv("CLAUDE_DEEP_AGENT_COMMAND", DEFAULT_CLAUDE_DEEP_AGENT_COMMAND).strip()
    if command:
        return command
    return os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)


def call_deep_agent(prompt: str, gateway_message) -> str:
    deep_prompt = build_niko_prompt(prompt, gateway_message, include_prompt_hook=True)
    return call_claude(deep_prompt, deep_agent_command())


def call_claude(prompt: str, command: str | None = None) -> str:
    command = command or os.getenv("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)
    return run_cli(command, prompt) or "(Khong co noi dung tra ve.)"


class NikoAgent:
    def __init__(self) -> None:
        self.deep_jobs: dict[str, DeepAgentJob] = {}
        self.deep_jobs_lock = threading.Lock()
        self.deep_agent_lock = threading.Lock()

    def conversation_id_for(self, gateway_message) -> str:
        return str(gateway_message.chat_id or gateway_message.user.key)

    def get_active_deep_job(self, conversation_id: str) -> DeepAgentJob | None:
        with self.deep_jobs_lock:
            return self.deep_jobs.get(conversation_id)

    def has_active_deep_job(self, conversation_id: str) -> bool:
        return self.get_active_deep_job(conversation_id) is not None

    def start_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> bool:
        with self.deep_jobs_lock:
            if conversation_id in self.deep_jobs:
                return False
            self.deep_jobs[conversation_id] = DeepAgentJob(
                conversation_id,
                gateway_message.user.key,
                prompt,
                time.time(),
            )

        thread = threading.Thread(
            target=self.run_deep_agent_job,
            args=(conversation_id, prompt, gateway_message, deliver_reply, notify_working),
            daemon=True,
        )
        thread.start()
        return True

    def run_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> None:
        try:
            with self.deep_agent_lock:
                if notify_working:
                    notify_working()
                answer = ensure_reply_suffix(call_deep_agent(prompt, gateway_message))

            deliver_reply(answer)
        except Exception as exc:
            deliver_reply(ensure_reply_suffix(f"Loi deep agent: {exc}"))
        finally:
            with self.deep_jobs_lock:
                self.deep_jobs.pop(conversation_id, None)

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        if two_agent_mode_enabled():
            return self.handle_two_agent_message(prompt, gateway_message, deliver_reply, notify_working)

        if notify_working:
            notify_working()
        answer = ensure_reply_suffix(call_deep_agent(prompt, gateway_message))
        deliver_reply(answer)
        return "deep_agent"

    def handle_two_agent_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        conversation_id = self.conversation_id_for(gateway_message)
        route = decide_agent_route(
            prompt,
            deep_job_active=self.has_active_deep_job(conversation_id),
            fast_agent_available=bool(fast_agent_command()),
        )
        print(f"Agent route: {route.kind} ({route.reason})")

        if route.kind == ROUTE_BUSY_REPLY:
            answer = ensure_reply_suffix(build_deep_busy_reply(self.get_active_deep_job(conversation_id)))
            deliver_reply(answer)
            return route.kind

        if route.kind == ROUTE_LOCAL_REPLY:
            answer = ensure_reply_suffix(route.reply)
            deliver_reply(answer)
            return route.kind

        if route.kind == ROUTE_FAST_AGENT:
            try:
                if notify_working:
                    notify_working()
                answer = ensure_reply_suffix(call_fast_agent(prompt, gateway_message))
                deliver_reply(answer)
                return route.kind
            except Exception as exc:
                print(f"Fast agent loi, chuyen sang deep agent: {exc}", file=sys.stderr)

        delay_before_deep_agent_if_needed(route.kind)
        started = self.start_deep_agent_job(conversation_id, prompt, gateway_message, deliver_reply, notify_working)
        answer = ensure_reply_suffix(build_deep_wait_reply() if started else build_deep_busy_reply(self.get_active_deep_job(conversation_id)))
        deliver_reply(answer)
        return route.kind
