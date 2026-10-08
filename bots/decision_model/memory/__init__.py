"""Public API cho các memory decision task của local Decision Model.

Package này giữ tương thích với import cũ `bots.decision_model.memory`,
đồng thời tách từng gate ra file riêng để dễ debug:

- `retrieval.py`: quyết định đọc/list memory trước Deep.
- `write.py`: quyết định có ghi episodic memory sau Deep không.
- `candidate.py`: phân loại candidate từ consolidation.
- `correction.py`: nhận diện intent sửa/xóa memory qua chat; đây là V1 tạm
  trước khi có Loop/tool workflow riêng cho memory mutation.
"""

from __future__ import annotations

from typing import Any

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice

from .candidate import (
    build_memory_candidate_criteria,
    build_memory_candidate_instructions,
    build_memory_candidate_state,
    classify_memory_candidate as _classify_memory_candidate,
    normalize_memory_candidate_choice,
)
from .common import decision_meta, normalize_optional_text, truncate_memory_gate_text
from .constants import (
    CANDIDATE_QUESTION_NAME,
    CORRECTION_QUESTION_NAME,
    MAX_MEMORY_GATE_PROMPT_LENGTH,
    MEMORY_CORRECT_MEMORY,
    MEMORY_CORRECTION_NONE,
    MEMORY_DISCARD,
    MEMORY_EPISODIC_EVENT,
    MEMORY_FORGET_MEMORY,
    MEMORY_LIST_FACTS,
    MEMORY_RECENT_EPISODES,
    MEMORY_REMEMBER,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_RETRIEVE,
    MEMORY_SEMANTIC_FACT,
    MEMORY_SKIP,
    MEMORY_TARGET_EPISODE,
    MEMORY_TARGET_FACT,
    MEMORY_TARGET_UNKNOWN,
    QUESTION_NAME,
    WRITE_QUESTION_NAME,
)
from .correction import (
    apply_memory_correction_prompt_hint,
    build_memory_correction_criteria,
    build_memory_correction_instructions,
    build_memory_correction_state,
    decide_memory_correction_intent as _decide_memory_correction_intent,
    extract_memory_correction_query_from_prompt,
    extract_memory_correction_replacement_from_prompt,
    is_memory_readonly_prompt,
    normalize_correction_prompt_text,
    normalize_memory_correction_choice,
    normalize_memory_correction_context,
    normalize_memory_target_type,
    normalize_pending_choices,
    normalize_recent_turns,
)
from .retrieval import (
    apply_memory_retrieval_prompt_hint,
    build_memory_retrieval_criteria,
    build_memory_retrieval_instructions,
    build_memory_retrieval_state,
    decide_memory_retrieval as _decide_memory_retrieval,
    default_retrieval_modes,
    memory_inventory_prompt_target,
    normalize_retrieval_prompt_text,
    normalize_episode_retrieval_mode,
    normalize_fact_retrieval_mode,
    normalize_memory_retrieval_choice,
)
from .types import (
    MemoryCandidateDecision,
    MemoryCorrectionDecision,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
)
from .write import (
    build_memory_write_criteria,
    build_memory_write_instructions,
    build_memory_write_state,
    decide_memory_write as _decide_memory_write,
    normalize_memory_write_choice,
)


def decide_memory_retrieval(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
) -> MemoryRetrievalDecision:
    """Wrapper tương thích để patch `bots.decision_model.memory.systemone_choice` vẫn có hiệu lực."""
    return _decide_memory_retrieval(prompt, gateway_message, config, choice_fn=systemone_choice)


def decide_memory_write(
    prompt: str,
    answer: str,
    followups: list[str] | None = None,
    gateway_message=None,
    route: str = "",
    config: DecisionModelConfig | None = None,
) -> MemoryWriteDecision:
    """Wrapper tương thích cho write gate."""
    return _decide_memory_write(prompt, answer, followups, gateway_message, route, config, choice_fn=systemone_choice)


def classify_memory_candidate(
    candidate: dict[str, Any],
    config: DecisionModelConfig | None = None,
) -> MemoryCandidateDecision:
    """Wrapper tương thích cho candidate classifier."""
    return _classify_memory_candidate(candidate, config, choice_fn=systemone_choice)


def decide_memory_correction_intent(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
    decision_context: dict[str, Any] | None = None,
) -> MemoryCorrectionDecision:
    """Wrapper tương thích cho correction gate."""
    return _decide_memory_correction_intent(
        prompt,
        gateway_message,
        config,
        decision_context,
        choice_fn=systemone_choice,
    )


__all__ = [
    "CANDIDATE_QUESTION_NAME",
    "CORRECTION_QUESTION_NAME",
    "ChoiceDecision",
    "DecisionModelConfig",
    "MAX_MEMORY_GATE_PROMPT_LENGTH",
    "MEMORY_CORRECT_MEMORY",
    "MEMORY_CORRECTION_NONE",
    "MEMORY_DISCARD",
    "MEMORY_EPISODIC_EVENT",
    "MEMORY_FORGET_MEMORY",
    "MEMORY_LIST_FACTS",
    "MEMORY_RECENT_EPISODES",
    "MEMORY_REMEMBER",
    "MEMORY_RETRIEVAL_LIST",
    "MEMORY_RETRIEVAL_NONE",
    "MEMORY_RETRIEVAL_RECENT",
    "MEMORY_RETRIEVAL_SEARCH",
    "MEMORY_RETRIEVE",
    "MEMORY_SEMANTIC_FACT",
    "MEMORY_SKIP",
    "MEMORY_TARGET_EPISODE",
    "MEMORY_TARGET_FACT",
    "MEMORY_TARGET_UNKNOWN",
    "MemoryCandidateDecision",
    "MemoryCorrectionDecision",
    "MemoryRetrievalDecision",
    "MemoryWriteDecision",
    "QUESTION_NAME",
    "WRITE_QUESTION_NAME",
    "apply_memory_correction_prompt_hint",
    "apply_memory_retrieval_prompt_hint",
    "build_memory_candidate_criteria",
    "build_memory_candidate_instructions",
    "build_memory_candidate_state",
    "build_memory_correction_criteria",
    "build_memory_correction_instructions",
    "build_memory_correction_state",
    "build_memory_retrieval_criteria",
    "build_memory_retrieval_instructions",
    "build_memory_retrieval_state",
    "build_memory_write_criteria",
    "build_memory_write_instructions",
    "build_memory_write_state",
    "classify_memory_candidate",
    "decide_memory_correction_intent",
    "decide_memory_retrieval",
    "decide_memory_write",
    "decision_meta",
    "default_retrieval_modes",
    "extract_memory_correction_query_from_prompt",
    "extract_memory_correction_replacement_from_prompt",
    "is_memory_readonly_prompt",
    "memory_inventory_prompt_target",
    "normalize_correction_prompt_text",
    "normalize_episode_retrieval_mode",
    "normalize_fact_retrieval_mode",
    "normalize_memory_candidate_choice",
    "normalize_memory_correction_choice",
    "normalize_memory_correction_context",
    "normalize_memory_retrieval_choice",
    "normalize_memory_target_type",
    "normalize_memory_write_choice",
    "normalize_optional_text",
    "normalize_pending_choices",
    "normalize_recent_turns",
    "normalize_retrieval_prompt_text",
    "systemone_choice",
    "truncate_memory_gate_text",
]
