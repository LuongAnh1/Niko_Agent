__all__ = ["NikoAgent"]


def __getattr__(name: str):
    if name == "NikoAgent":
        from graphs.chat_reply import ChatReplyGraph

        return ChatReplyGraph
    raise AttributeError(f"module 'niko' has no attribute {name!r}")
