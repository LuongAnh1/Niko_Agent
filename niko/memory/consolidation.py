"""Scaffold gom `chat_log` thành batch trước khi sinh long-term memory.

Module này mới làm lớp kiểm soát an toàn cho phase consolidation: đọc đúng các
dòng chat chưa xử lý và cung cấp thao tác mark-done tường minh. Việc gọi model
để tạo candidate fact/episode sẽ nằm ở phase sau, nhờ vậy scaffold này không tự
ghi memory dài hạn khi chưa có guardrail đủ rõ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from niko.memory.store import MemoryStore, default_memory_store


DEFAULT_CONSOLIDATION_BATCH_SIZE = 12


@dataclass(frozen=True)
class ConsolidationBatch:
    """Một lát cắt cố định của `chat_log` chưa consolidated."""

    rows: list[dict[str, Any]]

    @property
    def row_ids(self) -> list[int]:
        return [int(row["id"]) for row in self.rows]

    @property
    def rows_read(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "row_ids": self.row_ids,
            "rows_read": self.rows_read,
            "is_empty": self.is_empty,
        }


@dataclass(frozen=True)
class ConsolidationResult:
    """Kết quả thao tác scaffold, đủ metadata để đưa vào trace/dashboard sau này."""

    status: str
    rows_read: int
    row_ids: list[int]
    marked_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rows_read": self.rows_read,
            "row_ids": self.row_ids,
            "marked_count": self.marked_count,
            "reason": self.reason,
        }


class MemoryConsolidator:
    """API nhỏ cho phase consolidation, chưa tự gọi model hay ghi facts/episodes."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        batch_size: int = DEFAULT_CONSOLIDATION_BATCH_SIZE,
    ) -> None:
        self._store = store
        self.batch_size = max(1, int(batch_size))

    @property
    def store(self) -> MemoryStore:
        return self._store or default_memory_store()

    def preview_next_batch(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationBatch:
        """Đọc batch kế tiếp mà không ghi gì, dùng cho debug hoặc bước model sau."""
        batch_limit = self.batch_size if limit is None else max(1, int(limit))
        rows = self.store.list_unconsolidated_chat(batch_limit, session_id=session_id)
        return ConsolidationBatch(rows=rows)

    def mark_batch_done(self, row_ids: list[int] | tuple[int, ...], reason: str = "manual") -> ConsolidationResult:
        """Mark các row đã được xử lý xong bởi caller có guardrail riêng."""
        clean_ids: list[int] = []
        for row_id in row_ids:
            try:
                normalized_id = int(row_id)
            except (TypeError, ValueError):
                continue
            if normalized_id > 0:
                clean_ids.append(normalized_id)
        clean_ids = sorted(set(clean_ids))
        if not clean_ids:
            return ConsolidationResult(
                status="empty",
                rows_read=0,
                row_ids=[],
                marked_count=0,
                reason=reason or "no_row_ids",
            )

        marked_count = self.store.mark_chat_consolidated(clean_ids)
        return ConsolidationResult(
            status="marked",
            rows_read=len(clean_ids),
            row_ids=clean_ids,
            marked_count=marked_count,
            reason=reason,
        )


__all__ = [
    "ConsolidationBatch",
    "ConsolidationResult",
    "MemoryConsolidator",
    "DEFAULT_CONSOLIDATION_BATCH_SIZE",
]
