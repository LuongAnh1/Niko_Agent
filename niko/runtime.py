"""Runtime LLM của Niko.

Deep agent chính gọi DeepSeek qua Chat Completions API. Các helper CLI cũ vẫn
được giữ cho Fast/legacy fallback hoặc debug command-line, nhưng luồng Deep mặc
định không còn shell out sang `fcc-claude`.

File này nạp persona hook, thêm identity/memory context khi gọi Deep, chuẩn hóa
lỗi runtime thành `RuntimeError`, và ghi runtime log để dashboard quan sát. Nó
không chọn route và không tự quyết định memory nào cần retrieve; phần đó thuộc
graph hoặc `MemoryRuntime`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from niko.chat_gateway import build_identity_context
from niko.config import env_flag, env_value, resolve_project_path
from niko.harness.runtime_log import default_runtime_logger


DEFAULT_CLAUDE_COMMAND = "fcc-claude -p"
DEFAULT_CLAUDE_DEEP_AGENT_COMMAND = ""
DEFAULT_CLAUDE_WORKDIR = ""
DEFAULT_NIKO_PROMPT_HOOK_FILE = "niko/HOOK.md"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_TIMEOUT_SECONDS = "90"


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
    """Legacy CLI command của Deep agent; giữ tạm cho đường Claude/FCC cũ."""
    command = env_value("CLAUDE_DEEP_AGENT_COMMAND", DEFAULT_CLAUDE_DEEP_AGENT_COMMAND).strip()
    if command:
        return command
    return env_value("CLAUDE_CLI_COMMAND", DEFAULT_CLAUDE_COMMAND)


def call_deep_agent(
    prompt: str,
    gateway_message,
    trace_id: str | None = None,
    trace_logger=None,
    extra_context: str = "",
) -> str:
    """Gọi Deep agent kèm memory context và ghi trace retrieval nếu có."""
    return call_deep_agent_with_context(
        prompt,
        gateway_message,
        trace_id=trace_id,
        trace_logger=trace_logger,
        extra_context=extra_context,
    )


def call_deep_agent_with_context(
    prompt: str,
    gateway_message,
    trace_id: str | None = None,
    trace_logger=None,
    extra_context: str = "",
) -> str:
    """Gọi Deep agent với memory và context bổ sung từ workflow/tool."""
    memory_context = ""
    try:
        from niko.harness.trace import default_trace_logger
        from niko.memory.runtime import default_memory_runtime

        retrieved_memory = default_memory_runtime().retrieve_for_deep(prompt, gateway_message)
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

    combined_context = "\n\n".join(part.strip() for part in (memory_context, extra_context) if part.strip())
    if extra_context.strip() and trace_id:
        try:
            from niko.harness.trace import default_trace_logger

            logger = trace_logger or default_trace_logger()
            logger.event(trace_id, "deep_extra_context", {"chars": len(extra_context), "source": "graph_workflow"})
        except Exception:
            pass

    deep_prompt = build_niko_prompt(
        prompt,
        gateway_message,
        include_prompt_hook=True,
        prompt_label="Current user message",
        memory_context=combined_context,
    )
    return call_deepseek(deep_prompt)


def call_deepseek(prompt: str) -> str:
    """Gọi DeepSeek Chat Completions cho Deep agent chính của Niko."""
    api_key = env_value("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Thieu DEEPSEEK_API_KEY. Hay cau hinh trong dashboard Config truoc khi goi Deep.")

    base_url = env_value("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).strip() or DEFAULT_DEEPSEEK_BASE_URL
    model = env_value("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL
    timeout_seconds = _positive_int_env("DEEPSEEK_TIMEOUT_SECONDS", DEFAULT_DEEPSEEK_TIMEOUT_SECONDS)
    url = _deepseek_chat_url(base_url)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    default_runtime_logger().event(
        "deep_runtime",
        "deepseek_request",
        f"Goi DeepSeek model={model}",
        data={"model": model, "base_url": base_url, "prompt_chars": len(prompt)},
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw_response = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        default_runtime_logger().event(
            "deep_runtime",
            "deepseek_error",
            f"DeepSeek HTTP error: {exc.code}",
            level="error",
            data={"status": exc.code, "body": body},
        )
        raise RuntimeError(f"DeepSeek tra ve HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        default_runtime_logger().event(
            "deep_runtime",
            "deepseek_error",
            f"Loi ket noi DeepSeek: {exc.reason}",
            level="error",
            data={"error": str(exc.reason)},
        )
        raise RuntimeError(f"Loi ket noi DeepSeek: {exc.reason}") from exc
    except TimeoutError as exc:
        default_runtime_logger().event(
            "deep_runtime",
            "deepseek_error",
            "DeepSeek timeout",
            level="error",
            data={"timeout_seconds": timeout_seconds},
        )
        raise RuntimeError(f"DeepSeek chay qua lau, da het timeout {timeout_seconds}s.") from exc

    try:
        response_payload = json.loads(raw_response)
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        default_runtime_logger().event(
            "deep_runtime",
            "deepseek_error",
            "DeepSeek response khong dung schema",
            level="error",
            data={"body": raw_response},
        )
        raise RuntimeError("DeepSeek response khong dung schema Chat Completions.") from exc

    default_runtime_logger().event(
        "deep_runtime",
        "deepseek_response",
        f"DeepSeek da tra loi model={model}",
        data={"model": model, "reply_chars": len(str(content or ""))},
    )
    return str(content or "").strip() or "(Khong co noi dung tra ve.)"


def _deepseek_chat_url(base_url: str) -> str:
    normalized = str(base_url or DEFAULT_DEEPSEEK_BASE_URL).strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _positive_int_env(name: str, default: str) -> int:
    try:
        return max(1, int(env_value(name, default)))
    except ValueError:
        return int(default)


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
    "call_deep_agent_with_context",
    "deep_agent_command",
    "load_prompt_hook",
    "load_telegram_prompt_hook",
    "resolve_claude_workdir",
    "run_cli",
    "split_command",
]
