"""Consolidation thủ công để gom `chat_log` thành long-term memory.

Luồng hiện tại cố ý bảo thủ: rule nội bộ chỉ tạo candidate fact/episode, model
quyết định local chỉ phân loại candidate, rồi module này mới ghi vào `facts`
hoặc `episodes`. Nếu classifier lỗi thì batch không bị mark done để anh có thể
chạy lại sau, tránh mất dữ liệu hội thoại.

V1 chưa có scheduler/threshold tự động hoặc summarizer tự do. Dashboard/API là
caller duy nhất gọi `preview_next_batch` hoặc `run_once`; chat flow không tự bật
consolidation sau N tin nhắn cho tới khi có phase auto riêng.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from bots.decision_model.memory import (
    MEMORY_DISCARD,
    MEMORY_EPISODIC_EVENT,
    MEMORY_SEMANTIC_FACT,
    MemoryCandidateDecision,
    classify_memory_candidate,
)
from niko.memory.store import MemoryStore, default_memory_store


DEFAULT_CONSOLIDATION_BATCH_SIZE = 12
EXPLICIT_FACT_PATTERNS = (
    "ghi nhớ rằng",
    "ghi nho rang",
    "ghi nhớ",
    "ghi nho",
    "nhớ rằng",
    "nho rang",
    "lưu fact",
    "luu fact",
    "lưu rằng",
    "luu rang",
    "hãy nhớ",
    "hay nho",
    "từ giờ",
    "tu gio",
)
EPISODIC_KEYWORDS = (
    "debug",
    "fix",
    "sửa",
    "sua",
    "triển khai",
    "trien khai",
    "implement",
    "commit",
    "push",
    "kế hoạch",
    "ke hoach",
    "phân tích",
    "phan tich",
    "lỗi",
    "loi",
)


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


@dataclass(frozen=True)
class MemoryCandidate:
    """Ứng viên memory do rule nội bộ tạo ra trước khi hỏi classifier."""

    kind_hint: str
    subject: str = ""
    content: str = ""
    summary: str = ""
    reason: str = ""
    row_ids: list[int] | None = None
    conversation_id: str = ""
    source_roles: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind_hint": self.kind_hint,
            "subject": self.subject,
            "content": self.content,
            "summary": self.summary,
            "reason": self.reason,
            "row_ids": self.row_ids or [],
            "conversation_id": self.conversation_id,
            "source_roles": self.source_roles or [],
        }


@dataclass(frozen=True)
class ConsolidationRunResult:
    """Kết quả của một lần chạy manual, dùng cho API/dashboard và test."""

    status: str
    batch: ConsolidationBatch
    candidates: list[MemoryCandidate]
    decisions: list[dict[str, Any]]
    facts_written: list[int]
    episodes_written: list[int]
    marked_count: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "batch": self.batch.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "decisions": self.decisions,
            "facts_written": self.facts_written,
            "episodes_written": self.episodes_written,
            "marked_count": self.marked_count,
            "error": self.error,
        }


class MemoryConsolidator:
    """Pipeline consolidation thủ công: build candidate, classify, ghi và mark batch.

    Lớp này không có timer, scheduler hay threshold tự gọi. Caller phải chủ động
    preview/run từ dashboard, API hoặc test; phase auto sau này sẽ bọc quanh lớp
    này thay vì nhét vòng lặp nền vào đây.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        batch_size: int = DEFAULT_CONSOLIDATION_BATCH_SIZE,
        candidate_classifier=None,
    ) -> None:
        self._store = store
        self.batch_size = max(1, int(batch_size))
        self._candidate_classifier = candidate_classifier

    @property
    def store(self) -> MemoryStore:
        return self._store or default_memory_store()

    def preview_next_batch(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationBatch:
        """Đọc batch kế tiếp mà không ghi/mark gì; nút Refresh batch dùng đường này."""
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

    def build_candidates(self, batch: ConsolidationBatch) -> list[MemoryCandidate]:
        """Tạo candidate bằng rule bảo thủ, chưa ghi gì vào long-term memory."""
        if batch.is_empty:
            return []

        candidates: list[MemoryCandidate] = []
        explicit_fact = self._explicit_fact_candidate(batch)
        if explicit_fact is not None:
            # Lệnh ghi nhớ rõ ràng nên tạo semantic fact, tránh lưu thêm episodic event trùng nghĩa.
            candidates.append(explicit_fact)
            return candidates

        episodic_event = self._episodic_candidate(batch)
        if episodic_event is not None:
            candidates.append(episodic_event)

        if not candidates:
            candidates.append(
                MemoryCandidate(
                    kind_hint=MEMORY_DISCARD,
                    summary="Batch chỉ có tín hiệu thấp hoặc small talk.",
                    reason="no_conservative_candidate",
                    row_ids=batch.row_ids,
                    conversation_id=self._conversation_id(batch),
                    source_roles=self._source_roles(batch),
                )
            )
        return candidates

    def run_once(self, limit: int | None = None, session_id: str | None = None) -> ConsolidationRunResult:
        """Chạy một batch thủ công; lỗi classifier thì không mark rows."""
        batch = self.preview_next_batch(limit=limit, session_id=session_id)
        candidates = self.build_candidates(batch)
        if batch.is_empty:
            return ConsolidationRunResult("empty", batch, [], [], [], [], 0)

        decisions: list[dict[str, Any]] = []
        facts_written: list[int] = []
        episodes_written: list[int] = []
        try:
            for candidate in candidates:
                decision = self._classify_candidate(candidate)
                decisions.append(self._decision_to_dict(candidate, decision))
                if decision.decision == MEMORY_SEMANTIC_FACT and candidate.subject and candidate.content:
                    facts_written.append(
                        self.store.add_fact(
                            candidate.subject,
                            candidate.content,
                            source="consolidation",
                            meta={
                                "row_ids": candidate.row_ids or [],
                                "conversation_id": candidate.conversation_id,
                                "decision": self._decision_meta(decision),
                            },
                        )
                    )
                elif decision.decision == MEMORY_EPISODIC_EVENT and candidate.summary:
                    episodes_written.append(
                        self.store.add_episode(
                            candidate.summary,
                            source="consolidation",
                            meta={
                                "row_ids": candidate.row_ids or [],
                                "conversation_id": candidate.conversation_id,
                                "decision": self._decision_meta(decision),
                            },
                        )
                    )
        except Exception as exc:
            return ConsolidationRunResult(
                status="error",
                batch=batch,
                candidates=candidates,
                decisions=decisions,
                facts_written=facts_written,
                episodes_written=episodes_written,
                error=str(exc),
            )

        marked_count = self.store.mark_chat_consolidated(batch.row_ids)
        status = "discarded" if not facts_written and not episodes_written else "stored"
        return ConsolidationRunResult(
            status=status,
            batch=batch,
            candidates=candidates,
            decisions=decisions,
            facts_written=facts_written,
            episodes_written=episodes_written,
            marked_count=marked_count,
        )

    def _classify_candidate(self, candidate: MemoryCandidate) -> MemoryCandidateDecision:
        classifier = self._candidate_classifier or classify_memory_candidate
        return classifier(candidate.to_dict())

    def _explicit_fact_candidate(self, batch: ConsolidationBatch) -> MemoryCandidate | None:
        for row in batch.rows:
            if str(row.get("role", "")).lower() != "user":
                continue
            content = str(row.get("content", "")).strip()
            lowered = _normalize_text(content)
            if not any(pattern in lowered for pattern in EXPLICIT_FACT_PATTERNS):
                continue
            fact_text = _strip_explicit_fact_prefix(content)
            if not fact_text:
                continue
            return MemoryCandidate(
                kind_hint=MEMORY_SEMANTIC_FACT,
                subject="Chat memory",
                content=fact_text,
                reason="explicit_memory_statement",
                row_ids=batch.row_ids,
                conversation_id=self._conversation_id(batch),
                source_roles=self._source_roles(batch),
            )
        return None

    def _episodic_candidate(self, batch: ConsolidationBatch) -> MemoryCandidate | None:
        useful_rows = [row for row in batch.rows if _is_substantive_row(row)]
        if len(useful_rows) < 2:
            return None
        joined = " ".join(str(row.get("content", "")) for row in useful_rows)
        if not any(keyword in _normalize_text(joined) for keyword in EPISODIC_KEYWORDS):
            return None
        summary = _compact_batch_summary(useful_rows)
        if not summary:
            return None
        return MemoryCandidate(
            kind_hint=MEMORY_EPISODIC_EVENT,
            summary=summary,
            reason="substantive_chat_batch",
            row_ids=batch.row_ids,
            conversation_id=self._conversation_id(batch),
            source_roles=self._source_roles(batch),
        )

    @staticmethod
    def _conversation_id(batch: ConsolidationBatch) -> str:
        for row in batch.rows:
            session_id = str(row.get("session_id", "")).strip()
            if session_id:
                return session_id
        return ""

    @staticmethod
    def _source_roles(batch: ConsolidationBatch) -> list[str]:
        roles: list[str] = []
        for row in batch.rows:
            role = str(row.get("role", "")).strip()
            if role and role not in roles:
                roles.append(role)
        return roles

    @staticmethod
    def _decision_meta(decision: MemoryCandidateDecision) -> dict[str, Any]:
        data: dict[str, Any] = {
            "decision": decision.decision,
            "label": decision.label,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "model": decision.model,
            "probabilities": decision.probabilities,
        }
        return {key: value for key, value in data.items() if value not in ("", None, {})}

    def _decision_to_dict(self, candidate: MemoryCandidate, decision: MemoryCandidateDecision) -> dict[str, Any]:
        return {
            "candidate": candidate.to_dict(),
            **self._decision_meta(decision),
        }

def _normalize_text(text: str) -> str:
    lowered = str(text).casefold()
    replacements = {
        "á": "a",
        "à": "a",
        "ả": "a",
        "ã": "a",
        "ạ": "a",
        "ă": "a",
        "ắ": "a",
        "ằ": "a",
        "ẳ": "a",
        "ẵ": "a",
        "ặ": "a",
        "â": "a",
        "ấ": "a",
        "ầ": "a",
        "ẩ": "a",
        "ẫ": "a",
        "ậ": "a",
        "đ": "d",
        "é": "e",
        "è": "e",
        "ẻ": "e",
        "ẽ": "e",
        "ẹ": "e",
        "ê": "e",
        "ế": "e",
        "ề": "e",
        "ể": "e",
        "ễ": "e",
        "ệ": "e",
        "í": "i",
        "ì": "i",
        "ỉ": "i",
        "ĩ": "i",
        "ị": "i",
        "ó": "o",
        "ò": "o",
        "ỏ": "o",
        "õ": "o",
        "ọ": "o",
        "ô": "o",
        "ố": "o",
        "ồ": "o",
        "ổ": "o",
        "ỗ": "o",
        "ộ": "o",
        "ơ": "o",
        "ớ": "o",
        "ờ": "o",
        "ở": "o",
        "ỡ": "o",
        "ợ": "o",
        "ú": "u",
        "ù": "u",
        "ủ": "u",
        "ũ": "u",
        "ụ": "u",
        "ư": "u",
        "ứ": "u",
        "ừ": "u",
        "ử": "u",
        "ữ": "u",
        "ự": "u",
        "ý": "y",
        "ỳ": "y",
        "ỷ": "y",
        "ỹ": "y",
        "ỵ": "y",
    }
    for source, target in replacements.items():
        lowered = lowered.replace(source, target)
    return lowered


def _strip_explicit_fact_prefix(content: str) -> str:
    pattern = re.compile(
        r"^\s*(em\s+)?(hãy\s+nhớ\s+rằng|hay\s+nho\s+rang|hãy\s+nhớ|hay\s+nho|ghi\s+nhớ\s+rằng|ghi\s+nho\s+rang|ghi\s+nhớ|ghi\s+nho|nhớ\s+rằng|nho\s+rang|lưu\s+fact|luu\s+fact|lưu\s+rằng|luu\s+rang|từ\s+giờ|tu\s+gio)[:,\s-]*",
        flags=re.IGNORECASE,
    )
    stripped = pattern.sub("", content, count=1).strip()
    return stripped[:900]


def _is_substantive_row(row: dict[str, Any]) -> bool:
    content = str(row.get("content", "")).strip()
    if len(content) >= 40:
        return True
    return any(keyword in _normalize_text(content) for keyword in EPISODIC_KEYWORDS)


def _compact_batch_summary(rows: list[dict[str, Any]]) -> str:
    snippets: list[str] = []
    for row in rows[:6]:
        role = str(row.get("role", "chat")).strip() or "chat"
        content = " ".join(str(row.get("content", "")).split())
        if not content:
            continue
        if len(content) > 220:
            content = content[:217].rstrip() + "..."
        snippets.append(f"{role}: {content}")
    return "\n".join(snippets)[:1200]


__all__ = [
    "ConsolidationBatch",
    "ConsolidationResult",
    "ConsolidationRunResult",
    "MemoryCandidate",
    "MemoryConsolidator",
    "DEFAULT_CONSOLIDATION_BATCH_SIZE",
]
