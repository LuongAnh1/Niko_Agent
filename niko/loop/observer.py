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
        payload = data or {}
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
                    f"Loop event: {kind}",
                    data=payload,
                )
            except Exception:
                pass

