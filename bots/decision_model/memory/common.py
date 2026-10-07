"""Helper chung để chuẩn hóa text và metadata trace cho memory decisions."""

from __future__ import annotations

from typing import Any

from bots.decision_model.client import ChoiceDecision

from .constants import MAX_MEMORY_GATE_PROMPT_LENGTH
from .types import (
    MemoryCandidateDecision,
    MemoryCorrectionDecision,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
)


def normalize_optional_text(value: Any) -> str:
    """Chuẩn hóa field phụ `query/reason` nếu provider trả về."""
    if value is None:
        return ""
    return str(value).strip()


def truncate_memory_gate_text(value: str, limit: int = MAX_MEMORY_GATE_PROMPT_LENGTH) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


def decision_meta(
    decision: ChoiceDecision
    | MemoryRetrievalDecision
    | MemoryWriteDecision
    | MemoryCandidateDecision
    | MemoryCorrectionDecision,
) -> dict[str, Any]:
    """Metadata sạch để trace/log gate mà không cần lưu raw payload."""
    meta: dict[str, Any] = {}
    confidence = getattr(decision, "confidence", None)
    if confidence is not None:
        meta["confidence"] = confidence
    label = getattr(decision, "label", "") or getattr(decision, "choice", "")
    if label:
        meta["decision_label"] = label
    probabilities = getattr(decision, "probabilities", {})
    if probabilities:
        meta["probabilities"] = probabilities
    model = getattr(decision, "model", "")
    if model:
        meta["model"] = model
    usage = getattr(decision, "usage", {})
    if usage:
        meta["usage"] = usage
    return meta
