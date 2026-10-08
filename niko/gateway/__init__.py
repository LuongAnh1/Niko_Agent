"""Gateway runner chung cho các platform adapter của Niko.

Gateway cụ thể như Telegram chỉ nên làm IO/auth/parsing. Package này là lớp
mỏng ở giữa gateway và `NikoApp`, để các gateway sau này cùng gọi một điểm vào
trước khi app chọn các graph/workflow thật sự cần tách.
"""

from niko.gateway.runner import GatewayRunner, NotifyCallback, ReplyCallback

__all__ = ["GatewayRunner", "NotifyCallback", "ReplyCallback"]
