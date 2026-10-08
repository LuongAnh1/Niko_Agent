"""Memory tool adapters cho Loop core V0."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from niko.loop import Tool, ToolContext, ToolResult
from niko.memory.store import Fact, MemoryStore, default_memory_store


MemoryStoreProvider = Callable[[], MemoryStore]


def build_memory_fact_tools(store_provider: MemoryStoreProvider | None = None) -> list[Tool]:
    """Tạo bộ fact tools cho Loop mà chưa nối vào chat flow."""
    provider = store_provider or default_memory_store
    adapter = MemoryFactTools(provider)
    return [
        Tool(
            name="search_facts",
            description="Tìm semantic facts theo query text.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=adapter.search_facts,
            mutates_state=False,
        ),
        Tool(
            name="list_facts",
            description="Liệt kê semantic facts mới nhất.",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            },
            handler=adapter.list_facts,
            mutates_state=False,
        ),
        Tool(
            name="update_fact",
            description="Cập nhật subject/content của một semantic fact theo ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "fact_id": {"type": "integer", "minimum": 1},
                    "subject": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["fact_id", "subject", "content"],
            },
            handler=adapter.update_fact,
            mutates_state=True,
        ),
        Tool(
            name="delete_fact",
            description="Xóa một semantic fact theo ID.",
            input_schema={
                "type": "object",
                "properties": {"fact_id": {"type": "integer", "minimum": 1}},
                "required": ["fact_id"],
            },
            handler=adapter.delete_fact,
            mutates_state=True,
        ),
    ]


class MemoryFactTools:
    """Adapter mỏng từ Loop Tool contract sang MemoryStore facts."""

    def __init__(self, store_provider: MemoryStoreProvider) -> None:
        self._store_provider = store_provider

    @property
    def store(self) -> MemoryStore:
        return self._store_provider()

    def search_facts(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        query = _text_arg(args, "query")
        if not query:
            return _error("query_required", "query is required")
        top_k = _bounded_int_arg(args, "top_k", default=5, minimum=1, maximum=20)
        facts = self.store.search_facts(query, top_k=top_k)
        return ToolResult(
            ok=True,
            text=_facts_text(facts, empty_text="Không tìm thấy fact phù hợp."),
            data={"facts": [fact.to_dict() for fact in facts], "query": query, "top_k": top_k},
        )

    def list_facts(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        limit = _bounded_int_arg(args, "limit", default=10, minimum=1, maximum=50)
        facts = self.store.list_facts(limit=limit)
        return ToolResult(
            ok=True,
            text=_facts_text(facts, empty_text="Chưa có fact nào trong memory."),
            data={"facts": [fact.to_dict() for fact in facts], "limit": limit},
        )

    def update_fact(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        fact_id = _positive_int_arg(args, "fact_id")
        if fact_id is None:
            return _error("fact_id_required", "fact_id must be a positive integer")

        subject = _text_arg(args, "subject")
        content = _text_arg(args, "content")
        if not subject or not content:
            return _error("fact_content_required", "subject and content are required")

        updated = self.store.update_fact(fact_id, subject=subject, content=content)
        if not updated:
            return _error("fact_not_found", f"Fact #{fact_id} was not found", data={"fact_id": fact_id})

        return ToolResult(
            ok=True,
            text=f"Updated fact #{fact_id}: {subject} - {content}",
            data={"fact_id": fact_id, "subject": subject, "content": content, "updated": True},
        )

    def delete_fact(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        fact_id = _positive_int_arg(args, "fact_id")
        if fact_id is None:
            return _error("fact_id_required", "fact_id must be a positive integer")

        deleted = self.store.delete_fact(fact_id)
        if not deleted:
            return _error("fact_not_found", f"Fact #{fact_id} was not found", data={"fact_id": fact_id})

        return ToolResult(ok=True, text=f"Deleted fact #{fact_id}.", data={"fact_id": fact_id, "deleted": True})


def _text_arg(args: dict[str, Any], key: str) -> str:
    return str(args.get(key, "") or "").strip()


def _positive_int_arg(args: dict[str, Any], key: str) -> int | None:
    try:
        value = int(args.get(key, 0))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _bounded_int_arg(args: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _facts_text(facts: list[Fact], *, empty_text: str) -> str:
    if not facts:
        return empty_text
    return "\n".join(f"fact #{fact.id}: {fact.subject} - {fact.content}" for fact in facts)


def _error(error: str, text: str, data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(ok=False, text=text, data=data or {}, error=error)


__all__ = ["MemoryFactTools", "build_memory_fact_tools"]

