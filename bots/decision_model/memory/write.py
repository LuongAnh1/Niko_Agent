"""Decision task xác định Deep result có nên ghi thành episodic memory không."""

from __future__ import annotations

from collections.abc import Callable

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice

from .common import normalize_optional_text, truncate_memory_gate_text
from .constants import MEMORY_DISCARD, MEMORY_REMEMBER, MEMORY_SKIP, WRITE_QUESTION_NAME
from .types import MemoryWriteDecision


def decide_memory_write(
    prompt: str,
    answer: str,
    followups: list[str] | None = None,
    gateway_message=None,
    route: str = "",
    config: DecisionModelConfig | None = None,
    choice_fn: Callable[..., ChoiceDecision] = systemone_choice,
) -> MemoryWriteDecision:
    """Hỏi Nimble xem Deep result này có đáng ghi thành episodic memory không."""
    decision = choice_fn(
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
        "decision_context": (
            "Only decide whether to save this turn as long-term episodic memory. "
            "If the turn only inspects, lists, or confirms existing memory, prefer discard."
        ),
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

def build_memory_write_instructions() -> str:
    """Luật write gate: chỉ quyết định có lưu episodic memory sau Deep job không."""
    return (
        "Choose whether Niko should save this completed Deep-agent interaction "
        "as long-term episodic chat memory. Choose remember for substantive tasks, "
        "project decisions, user preferences, commitments, plans, debugging context, "
        "or information likely useful in a future conversation. Choose discard for "
        "small talk, transient acknowledgements, wait/busy/error replies, duplicate "
        "or low-signal content, and anything that should remain only in operational "
        "chat logs. Also choose discard for memory inspection or inventory turns "
        "where the user asks what Niko currently stores/remembers and the answer "
        "only reports existing memory; listing memory is not itself a durable event. "
        "If possible, include a short `reason`."
    )

def build_memory_write_criteria() -> dict[str, str]:
    """Hai lựa chọn duy nhất cho episodic write gate v1."""
    return {
        MEMORY_REMEMBER: "Save this completed Deep interaction as long-term episodic memory.",
        MEMORY_DISCARD: "Do not save this interaction as long-term episodic memory, including memory inspection/listing turns.",
    }

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
