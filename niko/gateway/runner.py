"""Runner mỏng nối gateway đã chuẩn hóa message vào workflow hiện tại.

Phase 1 của core split chỉ bọc đường gọi cũ: runner nhận prompt/callback từ
gateway rồi chuyển nguyên sang `ChatReplyGraph`. Nó chưa chọn workflow, chưa
biết Jira/memory policy và chưa thay đổi route/trace/log hiện có.
"""

from __future__ import annotations

from collections.abc import Callable

from niko.graphs.chat_reply import ChatReplyGraph


ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


class GatewayRunner:
    """Điểm vào chung cho gateway, hiện vẫn ủy quyền toàn bộ cho chat graph."""

    def __init__(self, chat_graph: ChatReplyGraph | None = None) -> None:
        self.chat_graph = chat_graph or ChatReplyGraph()

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        """Xử lý một turn từ gateway đã chuẩn hóa và trả route như graph cũ."""
        return self.chat_graph.handle_message(prompt, gateway_message, deliver_reply, notify_working)
