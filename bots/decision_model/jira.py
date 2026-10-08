"""Decision gate cho prompt Jira mơ hồ.

Nimble chỉ chọn một trong ba nhãn `use_jira_tool`, `ask_for_issue_key` hoặc
`skip_jira`. Prompt có issue key rõ vẫn đi qua rule Python cho nhanh và chắc;
prompt mơ hồ mới gửi recent turns vào model để nhận diện câu kiểu "ticket vừa
nãy". Model không tự gọi tool, không mutate dữ liệu và không sinh reply tự do.

Workflow gọi gate phải kiểm tra confidence, parse/validate issue key và quyết
định fallback. Confidence thấp là tín hiệu quay về route bảo thủ, không phải lý
do mở Jira tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice


JIRA_USE_TOOL = "use_jira_tool"
JIRA_ASK_FOR_ISSUE_KEY = "ask_for_issue_key"
JIRA_SKIP = "skip_jira"
QUESTION_NAME = "jira_gate"
MAX_JIRA_PROMPT_LENGTH = 1200


@dataclass(frozen=True)
class JiraGateDecision:
    """Kết quả gate đã chuẩn hóa để graph quyết định hành động tiếp theo."""

    decision: str
    issue_key: str = ""
    query: str = ""
    reason: str = ""
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def decide_jira_gate(
    prompt: str,
    *,
    issue_keys: list[str] | tuple[str, ...] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
) -> JiraGateDecision:
    """Hỏi Nimble xem prompt mơ hồ có cần Jira tools không."""
    decision = systemone_choice(
        state=build_jira_gate_state(
            prompt,
            issue_keys=issue_keys or [],
            recent_turns=recent_turns or [],
            gateway_message=gateway_message,
        ),
        question_name=QUESTION_NAME,
        instructions=build_jira_gate_instructions(),
        criteria=build_jira_gate_criteria(),
        config=config,
    )
    label = normalize_jira_gate_choice(decision.choice)
    return JiraGateDecision(
        decision=label,
        issue_key=str(decision.extra.get("issue_key") or "").strip().upper(),
        query=str(decision.extra.get("query") or "").strip(),
        reason=str(decision.extra.get("reason") or "").strip(),
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )


def build_jira_gate_state(
    prompt: str,
    *,
    issue_keys: list[str] | tuple[str, ...],
    recent_turns: list[dict[str, Any]],
    gateway_message=None,
) -> dict[str, Any]:
    """State đủ hẹp để model phân biệt chat thường với câu hỏi Jira/task."""
    state: dict[str, Any] = {
        "prompt": _truncate(prompt),
        "issue_keys_in_prompt": list(issue_keys),
        "recent_turns": [_compact_turn(turn) for turn in recent_turns[-6:]],
    }
    if gateway_message is not None:
        user = getattr(gateway_message, "user", None)
        state["gateway"] = {
            "platform": getattr(gateway_message, "platform", ""),
            "chat_type": getattr(gateway_message, "chat_type", ""),
            "user_label": getattr(user, "label", "") if user is not None else "",
        }
    return state


def build_jira_gate_instructions() -> str:
    """Luật gate: chỉ chọn tool khi user thật sự muốn phân tích Jira/task."""
    return (
        "Classify whether Niko should use Jira issue tools for this message. "
        "Choose use_jira_tool when the user asks to inspect, analyze, summarize, "
        "debug, or continue discussing a Jira issue/task/ticket and an issue key is "
        "present in the prompt or recent turns. Choose ask_for_issue_key when the "
        "user clearly wants Jira/task analysis but no issue key is available. "
        "Choose skip_jira for ordinary chat, memory correction, generic coding, or "
        "anything that should follow the normal chat route."
    )


def build_jira_gate_criteria() -> dict[str, str]:
    """Ba nhãn duy nhất mà graph Jira hiểu."""
    return {
        JIRA_USE_TOOL: "Use Jira tools because the turn targets a concrete Jira issue/task.",
        JIRA_ASK_FOR_ISSUE_KEY: "Ask the user for an issue key before using Jira tools.",
        JIRA_SKIP: "Do not use Jira tools; continue the normal chat route.",
    }


def normalize_jira_gate_choice(choice: str) -> str:
    """Chấp nhận alias nhỏ, nhãn ngoài contract bị coi là lỗi model."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        JIRA_USE_TOOL: JIRA_USE_TOOL,
        "use_jira": JIRA_USE_TOOL,
        "jira": JIRA_USE_TOOL,
        "tool": JIRA_USE_TOOL,
        "fetch_jira": JIRA_USE_TOOL,
        JIRA_ASK_FOR_ISSUE_KEY: JIRA_ASK_FOR_ISSUE_KEY,
        "ask_key": JIRA_ASK_FOR_ISSUE_KEY,
        "need_issue_key": JIRA_ASK_FOR_ISSUE_KEY,
        "missing_issue_key": JIRA_ASK_FOR_ISSUE_KEY,
        JIRA_SKIP: JIRA_SKIP,
        "skip": JIRA_SKIP,
        "none": JIRA_SKIP,
        "normal_chat": JIRA_SKIP,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama Jira gate tra label khong hop le: {choice or '(empty)'}") from exc


def _compact_turn(turn: dict[str, Any]) -> dict[str, str]:
    return {
        "role": str(turn.get("role") or "")[:30],
        "content": _truncate(str(turn.get("content") or ""), limit=400),
        "route": str(turn.get("route") or "")[:60],
    }


def _truncate(value: str, limit: int = MAX_JIRA_PROMPT_LENGTH) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"
