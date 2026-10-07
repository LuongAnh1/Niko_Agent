"""Helper dựng working memory ngắn hạn từ `chat_log`.

Working memory khác long-term facts/episodes: nó chỉ lấy vài lượt chat gần nhất
trong cùng conversation để Deep hiểu follow-up, rồi bỏ đi sau turn hiện tại. File
này cố ý không gọi Decision Model và không ghi database.
"""

from __future__ import annotations

from typing import Any

from niko.memory.store import MemoryStore


def recent_chat_window(
    store: MemoryStore,
    conversation_id: str,
    current_prompt: str = "",
    *,
    limit: int = 6,
    char_budget: int = 2400,
    per_turn_limit: int = 700,
) -> list[dict[str, str]]:
    """Lấy recent turns cũ -> mới, bỏ chính incoming prompt nếu đã được log."""
    limit = max(0, int(limit))
    char_budget = max(0, int(char_budget))
    per_turn_limit = max(80, int(per_turn_limit))
    if limit <= 0 or char_budget <= 0:
        return []

    try:
        rows = store.chat_history(conversation_id, limit=max(limit + 2, limit * 2))
    except Exception:
        return []

    clean_rows = list(rows)
    if clean_rows and current_prompt.strip():
        last = clean_rows[-1]
        if (
            str(last.get("role", "")).strip().lower() == "user"
            and str(last.get("content", "")).strip() == current_prompt.strip()
        ):
            clean_rows = clean_rows[:-1]

    selected_reversed: list[dict[str, str]] = []
    used_chars = 0
    for row in reversed(clean_rows):
        if len(selected_reversed) >= limit:
            break
        turn = normalize_chat_turn(row, per_turn_limit=per_turn_limit)
        if not turn:
            continue
        projected = used_chars + len(turn["content"])
        if selected_reversed and projected > char_budget:
            break
        if projected > char_budget:
            turn = {**turn, "content": truncate_text(turn["content"], char_budget)}
            projected = len(turn["content"])
        selected_reversed.append(turn)
        used_chars = projected

    return list(reversed(selected_reversed))


def normalize_chat_turn(row: dict[str, Any], *, per_turn_limit: int = 700) -> dict[str, str]:
    """Chuẩn hóa một row chat_log thành turn an toàn để đưa vào prompt."""
    role = str(row.get("role", "")).strip()
    content = truncate_text(str(row.get("content", "")), limit=per_turn_limit)
    if not role or not content:
        return {}

    turn = {
        "role": role,
        "content": content,
    }
    created_at = str(row.get("created_at", "")).strip()
    if created_at:
        turn["created_at"] = created_at
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    route = str(meta.get("route", "")).strip()
    if route:
        turn["route"] = route
    return turn


def truncate_text(value: str, limit: int = 700) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


__all__ = ["normalize_chat_turn", "recent_chat_window", "truncate_text"]
