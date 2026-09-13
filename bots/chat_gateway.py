from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class ChatIdentity:
    platform: str
    user_id: str = ""
    username: str = ""
    display_name: str = ""
    alias: str = ""
    language_code: str = ""
    is_bot: bool = False

    @property
    def key(self) -> str:
        if not self.user_id:
            return self.platform
        return f"{self.platform}:{self.user_id}"

    @property
    def mention(self) -> str:
        if not self.username:
            return ""
        return f"@{self.username}"

    @property
    def label(self) -> str:
        return self.alias or self.display_name or self.mention or self.key


@dataclass(frozen=True)
class ChatGatewayMessage:
    platform: str
    chat_id: str
    chat_type: str
    chat_title: str
    text: str
    user: ChatIdentity
    raw: dict[str, Any]

    def with_text(self, text: str) -> "ChatGatewayMessage":
        return replace(self, text=text)


def parse_user_aliases(raw_value: str, default_platform: str = "telegram") -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in raw_value.replace("\\n", ";").split(";"):
        if "=" not in item:
            continue

        key, alias = item.split("=", 1)
        key = key.strip()
        alias = alias.strip()
        if not key or not alias:
            continue

        if ":" not in key:
            key = f"{default_platform}:{key}"
        aliases[key] = alias
    return aliases


def parse_allowed_user_keys(raw_value: str, default_platform: str = "telegram") -> set[str]:
    allowed: set[str] = set()
    for item in raw_value.replace("\\n", ",").replace(";", ",").split(","):
        key = item.strip()
        if not key:
            continue
        if ":" not in key:
            key = f"{default_platform}:{key}"
        allowed.add(key)
    return allowed


def telegram_message_to_gateway(
    message: dict[str, Any],
    aliases: dict[str, str] | None = None,
) -> ChatGatewayMessage:
    aliases = aliases or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}

    user_id = _to_text(sender.get("id"))
    username = _to_text(sender.get("username"))
    display_name = _join_name(sender.get("first_name"), sender.get("last_name")) or username
    identity = ChatIdentity(
        platform="telegram",
        user_id=user_id,
        username=username,
        display_name=display_name,
        alias=aliases.get(f"telegram:{user_id}", ""),
        language_code=_to_text(sender.get("language_code")),
        is_bot=bool(sender.get("is_bot")),
    )

    return ChatGatewayMessage(
        platform="telegram",
        chat_id=_to_text(chat.get("id")),
        chat_type=_to_text(chat.get("type")),
        chat_title=_to_text(chat.get("title")) or _join_name(chat.get("first_name"), chat.get("last_name")),
        text=_to_text(message.get("text")).strip(),
        user=identity,
        raw=message,
    )


def format_identity_reply(message: ChatGatewayMessage) -> str:
    lines = [
        f"chat_id: {message.chat_id or '(unknown)'}",
        f"chat_type: {message.chat_type or '(unknown)'}",
        f"user_key: {message.user.key}",
        f"user_id: {message.user.user_id or '(unknown)'}",
        f"display_name: {message.user.display_name or '(unknown)'}",
    ]
    if message.user.alias:
        lines.append(f"alias: {message.user.alias}")
    if message.user.mention:
        lines.append(f"username: {message.user.mention}")
    return "\n".join(lines)


def build_identity_context(message: ChatGatewayMessage) -> str:
    lines = [
        "Thong tin nguoi dang chat:",
        f"- gateway: {message.platform}",
        f"- chat_id: {message.chat_id or '(unknown)'}",
        f"- chat_type: {message.chat_type or '(unknown)'}",
    ]
    if message.chat_title:
        lines.append(f"- chat_title: {message.chat_title}")

    lines.extend(
        [
            f"- user_key: {message.user.key}",
            f"- user_id: {message.user.user_id or '(unknown)'}",
            f"- user_name: {message.user.label}",
        ]
    )
    if message.user.mention:
        lines.append(f"- username: {message.user.mention}")
    if message.user.language_code:
        lines.append(f"- language_code: {message.user.language_code}")

    return "\n".join(lines)


def _join_name(*parts: Any) -> str:
    return " ".join(part for part in (_to_text(part) for part in parts) if part)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
