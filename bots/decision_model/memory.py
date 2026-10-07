"""Decision Model tasks cho chat memory của Niko.

Module này giữ các câu hỏi hẹp gửi tới Ollama/Nimble. Nó chỉ trả label và
metadata; việc search SQLite hay ghi memory vẫn thuộc `niko.memory`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice


MEMORY_SKIP = "skip"
MEMORY_RETRIEVE = "retrieve"
MEMORY_REMEMBER = "remember"
MEMORY_DISCARD = "discard"
MEMORY_SEMANTIC_FACT = "semantic_fact"
MEMORY_EPISODIC_EVENT = "episodic_event"
QUESTION_NAME = "memory_retrieval"
WRITE_QUESTION_NAME = "memory_write"
CANDIDATE_QUESTION_NAME = "memory_type"
MAX_MEMORY_GATE_PROMPT_LENGTH = 1600


@dataclass(frozen=True)
class MemoryRetrievalDecision:
    """Quyết định đã chuẩn hóa cho bước retrieval long-term memory."""

    decision: str
    query: str = ""
    reason: str = ""
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


def decide_memory_retrieval(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
) -> MemoryRetrievalDecision:
    """Hỏi Nimble xem turn Deep này có cần đọc chat memory dài hạn không."""
    decision = systemone_choice(
        state=build_memory_retrieval_state(prompt, gateway_message),
        question_name=QUESTION_NAME,
        instructions=build_memory_retrieval_instructions(),
        criteria=build_memory_retrieval_criteria(),
        config=config,
    )
    normalized = normalize_memory_retrieval_choice(decision.choice)
    return MemoryRetrievalDecision(
        decision=normalized,
        query=normalize_optional_text(decision.extra.get("query")),
        reason=normalize_optional_text(decision.extra.get("reason")),
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )


def decide_memory_write(
    prompt: str,
    answer: str,
    followups: list[str] | None = None,
    gateway_message=None,
    route: str = "",
    config: DecisionModelConfig | None = None,
) -> MemoryWriteDecision:
    """Hỏi Nimble xem Deep result này có đáng ghi thành episodic memory không."""
    decision = systemone_choice(
        state=build_memory_write_state(prompt, answer, followups, gateway_message, route),
        question_name=WRITE_QUESTION_NAME,
        instructions=build_memory_write_instructions(),
        criteria=build_memory_write_criteria(),
        config=config,
    )
    normalized = normalize_memory_write_choice(decision.choice)
    return MemoryWriteDecision(
        decision=normalized,
        reason=normalize_optional_text(decision.extra.get("reason")),
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )


def classify_memory_candidate(
    candidate: dict[str, Any],
    config: DecisionModelConfig | None = None,
) -> MemoryCandidateDecision:
    """Hỏi Nimble xem candidate là semantic fact, episodic event hay nên bỏ."""
    decision = systemone_choice(
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


def build_memory_retrieval_state(prompt: str, gateway_message=None) -> dict[str, Any]:
    """State tối thiểu: prompt hiện tại và metadata gateway không nhạy cảm."""
    state: dict[str, Any] = {"prompt": truncate_memory_gate_text(prompt)}
    if gateway_message is None:
        return state

    user = getattr(gateway_message, "user", None)
    state["gateway"] = {
        "platform": getattr(gateway_message, "platform", ""),
        "conversation_id": str(getattr(gateway_message, "chat_id", "") or getattr(user, "key", "")),
        "chat_type": getattr(gateway_message, "chat_type", ""),
        "user_key": getattr(user, "key", "") if user is not None else "",
        "user_label": getattr(user, "label", "") if user is not None else "",
    }
    return state


def build_memory_write_state(
    prompt: str,
    answer: str,
    followups: list[str] | None = None,
    gateway_message=None,
    route: str = "",
) -> dict[str, Any]:
    """State gọn cho write gate; không gửi raw trace/log dài vào decision model."""
    state: dict[str, Any] = {
        "prompt": truncate_memory_gate_text(prompt),
        "answer": truncate_memory_gate_text(answer),
        "followups": [truncate_memory_gate_text(item, limit=500) for item in (followups or []) if item.strip()][:5],
        "route": route,
    }
    if gateway_message is None:
        return state

    user = getattr(gateway_message, "user", None)
    state["gateway"] = {
        "platform": getattr(gateway_message, "platform", ""),
        "conversation_id": str(getattr(gateway_message, "chat_id", "") or getattr(user, "key", "")),
        "chat_type": getattr(gateway_message, "chat_type", ""),
        "user_key": getattr(user, "key", "") if user is not None else "",
        "user_label": getattr(user, "label", "") if user is not None else "",
    }
    return state


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


def build_memory_retrieval_instructions() -> str:
    """Luật retrieval gate: chỉ quyết định đọc memory hay bỏ qua."""
    return (
        "Choose whether Niko's Deep agent should retrieve long-term chat memory "
        "before answering this user message. Choose retrieve when the user asks "
        "about remembered facts, past conversations, preferences, people, projects, "
        "previous decisions, or anything that benefits from personal context. "
        "Choose skip for standalone questions, coding/debugging tasks with all "
        "needed context in the prompt, casual small talk, or generic knowledge. "
        "If the API supports extra answer fields and you choose retrieve, include "
        "a concise search query in `query` and a short explanation in `reason`."
    )


def build_memory_write_instructions() -> str:
    """Luật write gate: chỉ quyết định có lưu episodic memory sau Deep job không."""
    return (
        "Choose whether Niko should save this completed Deep-agent interaction "
        "as long-term episodic chat memory. Choose remember for substantive tasks, "
        "project decisions, user preferences, commitments, plans, debugging context, "
        "or information likely useful in a future conversation. Choose discard for "
        "small talk, transient acknowledgements, wait/busy/error replies, duplicate "
        "or low-signal content, and anything that should remain only in operational "
        "chat logs. If possible, include a short `reason`."
    )


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


def build_memory_retrieval_criteria() -> dict[str, str]:
    """Hai lựa chọn duy nhất mà memory pipeline hiểu ở v1."""
    return {
        MEMORY_SKIP: "Do not search long-term chat memory for this turn.",
        MEMORY_RETRIEVE: "Search long-term chat memory before building the Deep prompt.",
    }


def build_memory_write_criteria() -> dict[str, str]:
    """Hai lựa chọn duy nhất cho episodic write gate v1."""
    return {
        MEMORY_REMEMBER: "Save this completed Deep interaction as long-term episodic memory.",
        MEMORY_DISCARD: "Do not save this interaction as long-term episodic memory.",
    }


def build_memory_candidate_criteria() -> dict[str, str]:
    """Ba loại candidate mà consolidation v1 hiểu."""
    return {
        MEMORY_SEMANTIC_FACT: "Store as a durable semantic fact.",
        MEMORY_EPISODIC_EVENT: "Store as an episodic event summary.",
        MEMORY_DISCARD: "Do not store this candidate as long-term memory.",
    }


def normalize_memory_retrieval_choice(choice: str) -> str:
    """Chấp nhận alias nhẹ để model lệch chữ vẫn dùng được."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        MEMORY_SKIP: MEMORY_SKIP,
        "no": MEMORY_SKIP,
        "none": MEMORY_SKIP,
        "no_memory": MEMORY_SKIP,
        "dont_retrieve": MEMORY_SKIP,
        "do_not_retrieve": MEMORY_SKIP,
        MEMORY_RETRIEVE: MEMORY_RETRIEVE,
        "search": MEMORY_RETRIEVE,
        "use_memory": MEMORY_RETRIEVE,
        "read_memory": MEMORY_RETRIEVE,
        "retrieve_memory": MEMORY_RETRIEVE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory decision khong hop le: {choice or '(empty)'}") from exc


def normalize_memory_write_choice(choice: str) -> str:
    """Chấp nhận alias nhẹ cho quyết định ghi/bỏ episodic memory."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        MEMORY_REMEMBER: MEMORY_REMEMBER,
        "write": MEMORY_REMEMBER,
        "save": MEMORY_REMEMBER,
        "keep": MEMORY_REMEMBER,
        "store": MEMORY_REMEMBER,
        "record": MEMORY_REMEMBER,
        "save_memory": MEMORY_REMEMBER,
        "write_memory": MEMORY_REMEMBER,
        MEMORY_DISCARD: MEMORY_DISCARD,
        MEMORY_SKIP: MEMORY_DISCARD,
        "no": MEMORY_DISCARD,
        "none": MEMORY_DISCARD,
        "no_memory": MEMORY_DISCARD,
        "dont_remember": MEMORY_DISCARD,
        "do_not_remember": MEMORY_DISCARD,
        "do_not_save": MEMORY_DISCARD,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory write decision khong hop le: {choice or '(empty)'}") from exc


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
    decision: ChoiceDecision | MemoryRetrievalDecision | MemoryWriteDecision | MemoryCandidateDecision,
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
