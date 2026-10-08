"""Observer cho Loop core, có thể ghi trace mà không kéo dashboard vào runtime."""

from __future__ import annotations

from typing import Any, Protocol


class LoopObserver(Protocol):
    """Interface tối thiểu để nhận event của LoopRuntime."""

    def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        ...


class NoopLoopObserver:
    """Observer mặc định cho test/flow không cần trace."""

    def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        return None


class TraceLoopObserver:
    """Ghi event loop vào TraceLogger và optional runtime logger."""

    def __init__(
        self,
        trace_logger,
        trace_id: str = "",
        runtime_logger=None,
        runtime_source: str = "loop",
    ) -> None:
        self.trace_logger = trace_logger
        self.trace_id = trace_id
        self.runtime_logger = runtime_logger
        self.runtime_source = runtime_source

    def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        payload = dict(data or {})
        if self.trace_id and "trace_id" not in payload:
            payload["trace_id"] = self.trace_id
        if self.trace_logger is not None:
            try:
                self.trace_logger.event(self.trace_id, kind, payload)
            except Exception:
                pass
        if self.runtime_logger is not None:
            try:
                self.runtime_logger.event(
                    self.runtime_source,
                    kind,
                    _runtime_message(kind, payload),
                    data=payload,
                    level="warning" if "error" in kind or payload.get("error") else "info",
                )
            except Exception:
                pass


def _runtime_message(kind: str, payload: dict[str, Any]) -> str:
    """Tạo message ngắn để bảng Runtime Log đọc được loop step mà không mở JSON."""
    if kind == "loop_started":
        return (
            "Loop started: "
            f"tools={payload.get('tool_count', 0)} "
            f"max_iterations={payload.get('max_iterations', '-')}"
        )
    if kind == "loop_decision":
        decision = payload.get("kind") or "-"
        tool_name = payload.get("tool_name") or "-"
        reason = payload.get("reason") or "-"
        return f"Loop decision: iteration={payload.get('iteration', '-')} kind={decision} tool={tool_name} reason={reason}"
    if kind == "loop_tool_call_started":
        return (
            "Loop tool started: "
            f"iteration={payload.get('iteration', '-')} "
            f"tool={payload.get('tool_name') or '-'} "
            f"mutates_state={payload.get('mutates_state', False)}"
        )
    if kind == "loop_tool_call_finished":
        status = "ok" if payload.get("ok") else "error"
        return (
            "Loop tool finished: "
            f"iteration={payload.get('iteration', '-')} "
            f"tool={payload.get('tool_name') or '-'} "
            f"status={status}"
        )
    if kind == "loop_final_answer":
        return f"Loop final answer: iteration={payload.get('iteration', '-')}"
    if kind == "loop_limit_reached":
        return f"Loop limit reached: iterations={payload.get('iterations', '-')}"
    if kind == "loop_error":
        return f"Loop error: iteration={payload.get('iteration', '-')} error={payload.get('error', '-')}"
    return f"Loop event: {kind}"
