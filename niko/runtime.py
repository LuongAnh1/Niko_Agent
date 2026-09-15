from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

from niko.chat_gateway import build_identity_context
from niko.config import env_flag, env_value, resolve_project_path


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_DEEP_AGENT_COMMAND = ""
DEFAULT_CLAUDE_WORKDIR = ""
DEFAULT_NIKO_PROMPT_HOOK_FILE = "niko/HOOK.md"


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


def build_niko_prompt(
    user_prompt: str,
    gateway_message=None,
    include_prompt_hook: bool = True,
    prompt_label: str = "Tin nhan nguoi dung",
) -> str:
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

    parts.append(f"{prompt_label}:\n{user_prompt}")
    return "\n\n".join(parts)


build_telegram_prompt = build_niko_prompt
load_telegram_prompt_hook = load_prompt_hook


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
