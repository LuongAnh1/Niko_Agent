"""Runner mỏng nối gateway đã chuẩn hóa message vào app hiện tại.

Phase 2 của core split đưa wiring sang `NikoApp`: runner nhận prompt/callback từ
gateway rồi chuyển nguyên sang app. Nó chưa chọn workflow, chưa biết Jira/memory
policy và chưa thay đổi route/trace/log hiện có.
"""

from __future__ import annotations

from collections.abc import Callable

from niko.app import NikoApp, create_niko_app
from niko.graphs.chat_reply import ChatReplyGraph


ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


class GatewayRunner:
    """Điểm vào chung cho gateway, hiện vẫn ủy quyền toàn bộ cho app mỏng."""

    def __init__(self, app: NikoApp | None = None, chat_graph: ChatReplyGraph | None = None) -> None:
        if app is not None and chat_graph is not None:
            raise ValueError("Chi truyen mot trong hai tham so: app hoac chat_graph.")
        # `chat_graph` là compatibility path cho tests Phase 1; runtime thật dùng app mặc định.
        self.app = app or create_niko_app(chat_graph=chat_graph)

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        """Xử lý một turn từ gateway đã chuẩn hóa và trả route như app hiện tại."""
        return self.app.handle_message(prompt, gateway_message, deliver_reply, notify_working)
