"""Gateway runner chung cho các platform adapter của Niko.

Gateway cụ thể như Telegram chỉ nên làm IO/auth/parsing. Package này là lớp
mỏng ở giữa gateway và workflow hiện tại, để các gateway sau này cùng gọi một
điểm vào trước khi mình tách tiếp `NikoApp` và `TurnOrchestrator`.
"""

from niko.gateway.runner import GatewayRunner, NotifyCallback, ReplyCallback

__all__ = ["GatewayRunner", "NotifyCallback", "ReplyCallback"]
