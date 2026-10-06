"""Runtime gọi LLM local của Niko.

Ứng dụng không gọi API LLM trực tiếp. Nó build prompt có persona, identity và
memory context rồi shell out sang CLI đã cấu hình (`fcc-claude` hiện tại).
Nhờ vậy Telegram/graph không cần biết chi tiết model, workdir hay command line.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

from niko.chat_gateway import build_identity_context
from niko.config import env_flag, env_value, resolve_project_path
from niko.harness.runtime_log import default_runtime_logger


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_DEEP_AGENT_COMMAND = ""
DEFAULT_CLAUDE_WORKDIR = ""
DEFAULT_NIKO_PROMPT_HOOK_FILE = "niko/HOOK.md"


def split_command(command: str) -> list[str]:
    """Tách command theo luật shell phù hợp Windows/POSIX."""
    return shlex.split(command, posix=os.name != "nt")


def build_cli_args(command: str, prompt: str | None = None) -> list[str]:
    """Build argv cuối cùng; prompt có thể append cuối command hoặc thay `{prompt}`."""
    args = split_command(os.path.expandvars(command))
    if args:
        args[0] = shutil.which(args[0]) or args[0]

    if prompt is None:
        return args

    if any("{prompt}" in arg for arg in args):
        return [arg.replace("{prompt}", prompt) for arg in args]

    return [*args, prompt]


def resolve_claude_workdir() -> Path | None:
    """Tạo workdir riêng cho CLI nếu cấu hình, tránh chạy thẳng trên repo root."""
    raw_path = env_value("CLAUDE_WORKDIR", DEFAULT_CLAUDE_WORKDIR).strip()
    if not raw_path:
        return None

    path = resolve_project_path(raw_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_cli(command: str, prompt: str | None = None, timeout_seconds: int | None = None) -> str:
    """Chạy một CLI LLM và chuẩn hóa lỗi thành RuntimeError dễ trace."""
    if timeout_seconds is None:
        timeout_seconds = int(env_value("CLAUDE_TIMEOUT_SECONDS", "180"))

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
    """Đọc persona/hook của Niko từ file cấu hình."""
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


def build_niko_prompt(
    user_prompt: str,
    gateway_message=None,
    include_prompt_hook: bool = True,
    prompt_label: str = "Tin nhan nguoi dung",
    memory_context: str = "",
) -> str:
    """Ghép prompt theo thứ tự: hook -> identity -> memory -> nhiệm vụ hiện tại."""
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
    if memory_context.strip():
        parts.append(memory_context.strip())
    if not parts:
        return user_prompt

    parts.append(f"{prompt_label}:\n{user_prompt}")
    return "\n\n".join(parts)


build_telegram_prompt = build_niko_prompt
load_telegram_prompt_hook = load_prompt_hook


def deep_agent_command() -> str:
    """Deep có command riêng; nếu trống thì dùng command Claude mặc định."""
    command = env_value("CLAUDE_DEEP_AGENT_COMMAND", DEFAULT_CLAUDE_DEEP_AGENT_COMMAND).strip()
    if command:
        return command
    return env_value("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)


def call_deep_agent(prompt: str, gateway_message, trace_id: str | None = None, trace_logger=None) -> str:
    """Gọi Deep agent kèm memory context và ghi trace retrieval nếu có."""
    memory_context = ""
    try:
        from niko.harness.trace import default_trace_logger
        from niko.memory.context import retrieve_memory_context

        retrieved_memory = retrieve_memory_context(prompt, gateway_message)
        memory_context = retrieved_memory.text
        if trace_id:
            logger = trace_logger or default_trace_logger()
            logger.event(trace_id, "memory_retrieval", retrieved_memory.to_meta())
    except Exception as exc:
        if trace_id:
            try:
                from niko.harness.trace import default_trace_logger

                logger = trace_logger or default_trace_logger()
                logger.event(trace_id, "memory_retrieval_error", {"error": str(exc)})
            except Exception:
                pass
        default_runtime_logger().event(
            "deep_runtime",
            "memory_context_error",
            f"Khong nap duoc memory context: {exc}",
            level="error",
            data={"error": str(exc)},
        )

    deep_prompt = build_niko_prompt(
        prompt,
        gateway_message,
        include_prompt_hook=True,
        memory_context=memory_context,
    )
    return call_claude(deep_prompt, deep_agent_command())


def call_claude(prompt: str, command: str | None = None) -> str:
    command = command or env_value("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)
    return run_cli(command, prompt) or "(Khong co noi dung tra ve.)"


__all__ = [
    "DEFAULT_CLAUDE_COMMAND",
    "DEFAULT_CLAUDE_DEEP_AGENT_COMMAND",
    "DEFAULT_CLAUDE_WORKDIR",
    "DEFAULT_NIKO_PROMPT_HOOK_FILE",
    "build_cli_args",
    "build_niko_prompt",
    "build_telegram_prompt",
    "call_claude",
    "call_deep_agent",
    "deep_agent_command",
    "load_prompt_hook",
    "load_telegram_prompt_hook",
    "resolve_claude_workdir",
    "run_cli",
    "split_command",
]
