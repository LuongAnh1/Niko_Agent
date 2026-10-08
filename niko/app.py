"""Assembly root mỏng cho runtime hiện tại của Niko.

Phase 2 của core split chỉ gom wiring vào một chỗ: app sở hữu chat workflow và
expose một entrypoint chung cho gateway runner. Nó chưa chọn workflow cấp turn,
chưa tách Jira/memory correction khỏi `ChatReplyGraph`, và chưa đổi behavior
user-facing.
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
    """Assembly root hiện tại, tạm thời ủy quyền toàn bộ turn sang chat graph."""

    def __init__(
        self,
        chat_graph=None,
        memory_store: MemoryStore | None = None,
        memory_runtime: MemoryRuntime | None = None,
        trace_logger: TraceLogger | None = None,
        jira_workflow=None,
    ) -> None:
        if chat_graph is not None and any(dep is not None for dep in (memory_store, memory_runtime, trace_logger)):
            raise ValueError("Chi truyen chat_graph hoac cac dependency de tao graph, khong truyen ca hai.")
        # Jira workflow vẫn do ChatReplyGraph chọn trong Phase 2; slot này giữ điểm ráp cho Orchestrator sau.
        self.jira_workflow = jira_workflow
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
        """Xử lý một turn đã chuẩn hóa và trả route như chat graph hiện tại."""
        return self.chat_graph.handle_message(prompt, gateway_message, deliver_reply, notify_working)


def create_niko_app(
    chat_graph=None,
    memory_store: MemoryStore | None = None,
    memory_runtime: MemoryRuntime | None = None,
    trace_logger: TraceLogger | None = None,
    jira_workflow=None,
) -> NikoApp:
    """Tạo app mặc định; tham số test-only giúp giữ đường inject graph hiện có."""
    return NikoApp(
        chat_graph=chat_graph,
        memory_store=memory_store,
        memory_runtime=memory_runtime,
        trace_logger=trace_logger,
        jira_workflow=jira_workflow,
    )
