"""Assembly root mỏng cho runtime hiện tại của Niko.

Theo tinh thần Waku, app là nơi ráp config/dependency và là entrypoint cấp turn
cho gateway. Khi chưa có workflow selection thật sự ở ngoài `ChatReplyGraph`, app
không tạo thêm lớp orchestrator chỉ để forward, tránh làm cấu trúc rối hơn luồng
đang chạy.
"""

from __future__ import annotations

from collections.abc import Callable

from niko.graphs.chat_reply import ChatReplyGraph
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore


ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


class NikoApp:
    """Assembly root hiện tại, chuyển turn đã chuẩn hóa vào chat graph."""

    def __init__(
        self,
        chat_graph=None,
        memory_store: MemoryStore | None = None,
        memory_runtime: MemoryRuntime | None = None,
        trace_logger: TraceLogger | None = None,
    ) -> None:
        if chat_graph is not None and any(dep is not None for dep in (memory_store, memory_runtime, trace_logger)):
            raise ValueError("Chi truyen chat_graph hoac cac dependency de tao graph, khong truyen ca hai.")

        self.chat_graph = chat_graph or ChatReplyGraph(
            memory_store=memory_store,
            memory_runtime=memory_runtime,
            trace_logger=trace_logger,
        )

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        """Xử lý một turn đã chuẩn hóa và trả route do chat graph quyết định."""
        return self.chat_graph.handle_message(prompt, gateway_message, deliver_reply, notify_working)


def create_niko_app(
    chat_graph=None,
    memory_store: MemoryStore | None = None,
    memory_runtime: MemoryRuntime | None = None,
    trace_logger: TraceLogger | None = None,
) -> NikoApp:
    """Tạo app mặc định; tham số test-only giúp giữ đường inject graph hiện có."""
    return NikoApp(
        chat_graph=chat_graph,
        memory_store=memory_store,
        memory_runtime=memory_runtime,
        trace_logger=trace_logger,
    )
