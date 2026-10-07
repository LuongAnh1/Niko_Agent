"""Dataclass kết quả đã chuẩn hóa cho từng memory decision gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import MEMORY_RETRIEVAL_SEARCH, MEMORY_TARGET_UNKNOWN


@dataclass(frozen=True)
class MemoryRetrievalDecision:
    """Quyết định retrieval đã chuẩn hóa, gồm nhãn retrieve/skip và mode facts/episodes."""

    decision: str
    query: str = ""
    reason: str = ""
    fact_mode: str = MEMORY_RETRIEVAL_SEARCH
    episode_mode: str = MEMORY_RETRIEVAL_SEARCH
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryWriteDecision:
    """Quyết định đã chuẩn hóa cho bước ghi episodic memory dài hạn."""

    decision: str
    reason: str = ""
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryCandidateDecision:
    """Quyết định đã chuẩn hóa cho một memory candidate từ consolidation."""

    decision: str
    reason: str = ""
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryCorrectionDecision:
    """Quyết định intent sửa/xóa memory; model không được tự mutate dữ liệu."""

    decision: str
    query: str = ""
    target_type: str = MEMORY_TARGET_UNKNOWN
    replacement: str = ""
    reason: str = ""
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
