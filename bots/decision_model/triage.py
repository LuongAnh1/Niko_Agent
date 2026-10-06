"""Triage local: prompt Telegram nên trả lời ngay hay đưa sang Deep.

File này là "policy adapter" giữa ChatReplyGraph và Nimble. Nimble chỉ chọn
label; graph vẫn là nơi quyết định hành động tiếp theo như gọi Fable để sinh
reply nhanh, queue Deep job, hay fallback khi lỗi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice


ROUTE_REPLY_NOW = "reply_now"
ROUTE_SEND_TO_DEEP = "send_to_deep"
QUESTION_NAME = "route"


@dataclass(frozen=True)
class DecisionRoute:
    """Quyết định đã được chuẩn hóa về route nội bộ của Niko."""

    route: str
    reply: str = ""
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def decide_fast_route(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
) -> DecisionRoute:
    """Gọi Nimble và map label về `reply_now` hoặc `send_to_deep`."""
    decision = systemone_choice(
        state=build_triage_state(prompt, gateway_message),
        question_name=QUESTION_NAME,
        instructions=build_triage_instructions(),
        criteria=build_triage_criteria(),
        config=config,
    )
    route = normalize_route_choice(decision.choice)
    return DecisionRoute(
        route=route,
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )


def build_triage_state(prompt: str, gateway_message=None) -> dict[str, Any]:
    """State gửi cho Nimble: prompt chính + một ít metadata gateway không nhạy cảm."""
    state: dict[str, Any] = {"prompt": prompt}
    if gateway_message is None:
        return state

    user = getattr(gateway_message, "user", None)
    state["gateway"] = {
        "platform": getattr(gateway_message, "platform", ""),
        "chat_type": getattr(gateway_message, "chat_type", ""),
        "chat_title": getattr(gateway_message, "chat_title", ""),
        "user_label": getattr(user, "label", "") if user is not None else "",
        "username": getattr(user, "mention", "") if user is not None else "",
        "language_code": getattr(user, "language_code", "") if user is not None else "",
    }
    return state


def build_triage_instructions() -> str:
    """Luật triage ngắn gọn, tránh biến Nimble thành agent sinh câu trả lời."""
    return (
        "Classify whether Niko should answer this Telegram message immediately "
        "or send it to the Deep agent. Choose reply_now only for casual small talk, "
        "simple everyday questions, or a short response that does not require tools, "
        "memory, files, code work, planning, or careful analysis. Choose send_to_deep "
        "for coding, debugging, architecture, project memory, documents, APIs, Jira, "
        "GitHub, multi-step reasoning, personalized recall, or whenever uncertain."
    )


def build_triage_criteria() -> dict[str, str]:
    """Hai lựa chọn duy nhất mà graph hiểu ở bước triage."""
    return {
        ROUTE_REPLY_NOW: "Niko can answer now with a brief conversational reply.",
        ROUTE_SEND_TO_DEEP: "Niko should hand off to Deep for analysis, tools, memory, or uncertainty.",
    }


def normalize_route_choice(choice: str) -> str:
    """Chấp nhận một vài alias để nếu model lệch chữ nhẹ thì vẫn route được."""
    normalized = choice.strip().lower()
    aliases = {
        "reply": ROUTE_REPLY_NOW,
        "answer": ROUTE_REPLY_NOW,
        "respond": ROUTE_REPLY_NOW,
        ROUTE_REPLY_NOW: ROUTE_REPLY_NOW,
        "deep": ROUTE_SEND_TO_DEEP,
        "handoff": ROUTE_SEND_TO_DEEP,
        "handoff_to_deep": ROUTE_SEND_TO_DEEP,
        ROUTE_SEND_TO_DEEP: ROUTE_SEND_TO_DEEP,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama decision model tra route khong hop le: {choice or '(empty)'}") from exc


def route_meta(decision: ChoiceDecision | DecisionRoute) -> dict[str, Any]:
    """Metadata sạch để nhét vào trace mà không rò payload raw."""
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
