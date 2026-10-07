"""Decision task phân loại candidate do consolidation pipeline tạo sẵn."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice

from .common import normalize_optional_text, truncate_memory_gate_text
from .constants import (
    CANDIDATE_QUESTION_NAME,
    MEMORY_DISCARD,
    MEMORY_EPISODIC_EVENT,
    MEMORY_SEMANTIC_FACT,
    MEMORY_SKIP,
)
from .types import MemoryCandidateDecision


def classify_memory_candidate(
    candidate: dict[str, Any],
    config: DecisionModelConfig | None = None,
    choice_fn: Callable[..., ChoiceDecision] = systemone_choice,
) -> MemoryCandidateDecision:
    """Hỏi Nimble xem candidate là semantic fact, episodic event hay nên bỏ."""
    decision = choice_fn(
        state=build_memory_candidate_state(candidate),
        question_name=CANDIDATE_QUESTION_NAME,
        instructions=build_memory_candidate_instructions(),
        criteria=build_memory_candidate_criteria(),
        config=config,
    )
    normalized = normalize_memory_candidate_choice(decision.choice)
    return MemoryCandidateDecision(
        decision=normalized,
        reason=normalize_optional_text(decision.extra.get("reason")),
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )

def build_memory_candidate_state(candidate: dict[str, Any]) -> dict[str, Any]:
    """State hẹp cho classifier: candidate đã được pipeline tạo sẵn, không phải raw log dài."""
    allowed_keys = {
        "kind_hint",
        "subject",
        "content",
        "summary",
        "reason",
        "source_roles",
        "row_ids",
        "conversation_id",
    }
    state: dict[str, Any] = {}
    for key in allowed_keys:
        value = candidate.get(key)
        if isinstance(value, str):
            state[key] = truncate_memory_gate_text(value, limit=900)
        elif value not in (None, [], {}):
            state[key] = value
    return state

def build_memory_candidate_instructions() -> str:
    """Luật phân loại candidate: model chỉ chọn loại, không tạo nội dung mới."""
    return (
        "Classify this pre-built chat-memory candidate. Choose semantic_fact only "
        "when the candidate is a stable user preference, standing fact, rule, "
        "identity detail, project detail, or instruction likely useful later. "
        "Choose episodic_event when it is a dated interaction, completed task, "
        "decision, debugging outcome, or follow-up context. Choose discard for "
        "small talk, vague statements, unsupported inference, duplicates, transient "
        "messages, or anything too risky to store as long-term memory. Do not "
        "rewrite or add facts; only choose the label and optionally include reason."
    )

def build_memory_candidate_criteria() -> dict[str, str]:
    """Ba loại candidate mà consolidation v1 hiểu."""
    return {
        MEMORY_SEMANTIC_FACT: "Store as a durable semantic fact.",
        MEMORY_EPISODIC_EVENT: "Store as an episodic event summary.",
        MEMORY_DISCARD: "Do not store this candidate as long-term memory.",
    }

def normalize_memory_candidate_choice(choice: str) -> str:
    """Chấp nhận alias nhẹ cho classifier candidate."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        MEMORY_SEMANTIC_FACT: MEMORY_SEMANTIC_FACT,
        "fact": MEMORY_SEMANTIC_FACT,
        "semantic": MEMORY_SEMANTIC_FACT,
        "remember_fact": MEMORY_SEMANTIC_FACT,
        MEMORY_EPISODIC_EVENT: MEMORY_EPISODIC_EVENT,
        "episode": MEMORY_EPISODIC_EVENT,
        "episodic": MEMORY_EPISODIC_EVENT,
        "event": MEMORY_EPISODIC_EVENT,
        "memory_event": MEMORY_EPISODIC_EVENT,
        MEMORY_DISCARD: MEMORY_DISCARD,
        MEMORY_SKIP: MEMORY_DISCARD,
        "no": MEMORY_DISCARD,
        "none": MEMORY_DISCARD,
        "ignore": MEMORY_DISCARD,
        "do_not_store": MEMORY_DISCARD,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory candidate decision khong hop le: {choice or '(empty)'}") from exc
