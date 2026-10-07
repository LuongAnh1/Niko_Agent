"""Decision task nhận diện yêu cầu sửa/xóa chat memory dài hạn.

Đây là lớp Phase 5 V1 tạm thời trước khi Niko có Loop/tool workflow đúng nghĩa.
File này chỉ phụ trách hỏi Nimble và chuẩn hóa intent; việc tìm fact, hỏi lại
khi match mơ hồ, validate ID và update/delete SQLite vẫn nằm trong runtime
memory để giữ mutate dữ liệu ở phía Python.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any
import unicodedata

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice

from .common import normalize_optional_text, truncate_memory_gate_text
from .constants import (
    CORRECTION_QUESTION_NAME,
    MEMORY_CORRECT_MEMORY,
    MEMORY_CORRECTION_NONE,
    MEMORY_DISCARD,
    MEMORY_FORGET_MEMORY,
    MEMORY_SKIP,
    MEMORY_TARGET_EPISODE,
    MEMORY_TARGET_FACT,
    MEMORY_TARGET_UNKNOWN,
)
from .types import MemoryCorrectionDecision


def decide_memory_correction_intent(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
    decision_context: dict[str, Any] | None = None,
    choice_fn: Callable[..., ChoiceDecision] = systemone_choice,
) -> MemoryCorrectionDecision:
    """Hỏi Nimble xem user có đang yêu cầu sửa/xóa memory dài hạn không."""
    decision = choice_fn(
        state=build_memory_correction_state(prompt, gateway_message, decision_context=decision_context),
        question_name=CORRECTION_QUESTION_NAME,
        instructions=build_memory_correction_instructions(),
        criteria=build_memory_correction_criteria(),
        config=config,
    )
    normalized = normalize_memory_correction_choice(decision.choice)
    normalized = apply_memory_correction_prompt_hint(prompt, normalized)
    replacement = normalize_optional_text(decision.extra.get("replacement"))
    if normalized == MEMORY_CORRECT_MEMORY and not replacement:
        replacement = extract_memory_correction_replacement_from_prompt(prompt)
    return MemoryCorrectionDecision(
        decision=normalized,
        query=normalize_optional_text(decision.extra.get("query")),
        target_type=normalize_memory_target_type(decision.extra.get("target_type")),
        replacement=replacement,
        reason=normalize_optional_text(decision.extra.get("reason")),
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )

def build_memory_correction_state(
    prompt: str,
    gateway_message=None,
    decision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """State hẹp cho intent correction, ưu tiên current prompt và vài turn chat gần nhất."""
    state: dict[str, Any] = {
        "current_prompt": truncate_memory_gate_text(prompt),
        # Giữ key cũ để test/caller cũ không gãy trong lúc chuyển sang current_prompt.
        "prompt": truncate_memory_gate_text(prompt),
        "recent_turns": [],
        "active_workflow": "",
        "pending_action": "",
        "pending_choices": [],
        "pending_replacement": "",
        "decision_context": (
            "Only detect whether the user explicitly asks Niko to correct or forget "
            "existing long-term chat memory. Treat current_prompt plus recent_turns "
            "as the primary conversation context. Use active_workflow and pending_* "
            "only as auxiliary metadata to resolve short references and allowed IDs. "
            "Do not execute changes."
        ),
    }
    if decision_context:
        state.update(normalize_memory_correction_context(decision_context))
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


def normalize_memory_correction_context(context: dict[str, Any]) -> dict[str, Any]:
    """Chuẩn hóa context ngắn hạn trước khi gửi sang Nimble."""
    return {
        "recent_turns": normalize_recent_turns(context.get("recent_turns")),
        "active_workflow": normalize_optional_text(context.get("active_workflow")),
        "pending_action": normalize_optional_text(context.get("pending_action")),
        "pending_choices": normalize_pending_choices(context.get("pending_choices")),
        "pending_replacement": truncate_memory_gate_text(
            normalize_optional_text(context.get("pending_replacement")),
            limit=500,
        ),
    }


def normalize_recent_turns(value: Any) -> list[dict[str, str]]:
    """Giữ vài turn chat gần nhất làm ngữ cảnh chính cho model hiểu follow-up."""
    if not isinstance(value, list):
        return []
    turns: list[dict[str, str]] = []
    for item in value[-8:]:
        if not isinstance(item, dict):
            continue
        role = normalize_optional_text(item.get("role"))
        content = truncate_memory_gate_text(normalize_optional_text(item.get("content")), limit=700)
        if not role or not content:
            continue
        turn = {"role": role, "content": content}
        route = normalize_optional_text(item.get("route"))
        if route:
            turn["route"] = route
        created_at = normalize_optional_text(item.get("created_at"))
        if created_at:
            turn["created_at"] = created_at
        turns.append(turn)
    return turns


def normalize_pending_choices(value: Any) -> list[dict[str, Any]]:
    """Metadata phụ cho lựa chọn đang chờ: chỉ mang ID/type, không nhồi fact content."""
    if not isinstance(value, list):
        return []
    choices: list[dict[str, Any]] = []
    for item in value[:10]:
        if isinstance(item, dict):
            raw_id = item.get("id")
            choice_type = normalize_optional_text(item.get("type")) or MEMORY_TARGET_FACT
        else:
            raw_id = item
            choice_type = MEMORY_TARGET_FACT
        try:
            choice_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if choice_id <= 0:
            continue
        choices.append({"type": choice_type, "id": choice_id})
    return choices

def build_memory_correction_instructions() -> str:
    """Luật intent correction: model chỉ chọn intent và trích query/replacement."""
    return (
        "Classify whether the user explicitly asks Niko to modify long-term chat memory. "
        "Use `current_prompt` together with `recent_turns` as the primary context. "
        "Use `active_workflow`, `pending_action`, and `pending_choices` only as "
        "auxiliary state when the recent conversation shows the current prompt is a "
        "short follow-up such as 'fact #8', 'cái đó', or 'cái đầu tiên'. "
        "Choose none for ordinary questions, memory lookup/listing, new facts to remember, "
        "small talk, or vague complaints. Choose forget_memory only when the user clearly "
        "asks to delete, remove, forget, or stop remembering an existing memory. Choose "
        "correct_memory only when the user clearly asks to fix, change, update, or replace "
        "an existing memory. If possible, include concise extra fields: `query` to find the "
        "old memory, `target_type` as fact/episode/unknown, `replacement` for correct_memory, "
        "and `reason`. Never invent a memory id and never claim the change was applied."
    )

def build_memory_correction_criteria() -> dict[str, str]:
    """Ba nhãn intent mà memory correction v1 hiểu."""
    return {
        MEMORY_CORRECTION_NONE: "No memory correction or deletion request is present.",
        MEMORY_CORRECT_MEMORY: "The user explicitly wants to correct/update an existing memory.",
        MEMORY_FORGET_MEMORY: "The user explicitly wants to delete/forget an existing memory.",
    }

def normalize_memory_correction_choice(choice: str) -> str:
    """Chấp nhận alias nhẹ cho intent sửa/xóa memory."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        MEMORY_CORRECTION_NONE: MEMORY_CORRECTION_NONE,
        MEMORY_SKIP: MEMORY_CORRECTION_NONE,
        MEMORY_DISCARD: MEMORY_CORRECTION_NONE,
        "no": MEMORY_CORRECTION_NONE,
        "ignore": MEMORY_CORRECTION_NONE,
        "no_correction": MEMORY_CORRECTION_NONE,
        MEMORY_CORRECT_MEMORY: MEMORY_CORRECT_MEMORY,
        "correct": MEMORY_CORRECT_MEMORY,
        "update": MEMORY_CORRECT_MEMORY,
        "edit_memory": MEMORY_CORRECT_MEMORY,
        "fix_memory": MEMORY_CORRECT_MEMORY,
        "replace_memory": MEMORY_CORRECT_MEMORY,
        MEMORY_FORGET_MEMORY: MEMORY_FORGET_MEMORY,
        "forget": MEMORY_FORGET_MEMORY,
        "delete": MEMORY_FORGET_MEMORY,
        "remove": MEMORY_FORGET_MEMORY,
        "delete_memory": MEMORY_FORGET_MEMORY,
        "remove_memory": MEMORY_FORGET_MEMORY,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory correction decision khong hop le: {choice or '(empty)'}") from exc


def normalize_memory_target_type(value: Any) -> str:
    """Chuẩn hóa target_type phụ; thiếu thì coi là unknown để Python tự guard."""
    normalized = normalize_optional_text(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": MEMORY_TARGET_UNKNOWN,
        MEMORY_TARGET_UNKNOWN: MEMORY_TARGET_UNKNOWN,
        "memory": MEMORY_TARGET_UNKNOWN,
        "chat_memory": MEMORY_TARGET_UNKNOWN,
        MEMORY_TARGET_FACT: MEMORY_TARGET_FACT,
        "facts": MEMORY_TARGET_FACT,
        "semantic": MEMORY_TARGET_FACT,
        "semantic_fact": MEMORY_TARGET_FACT,
        MEMORY_TARGET_EPISODE: MEMORY_TARGET_EPISODE,
        "episodes": MEMORY_TARGET_EPISODE,
        "episodic": MEMORY_TARGET_EPISODE,
        "episodic_event": MEMORY_TARGET_EPISODE,
    }
    try:
        return aliases[normalized]
    except KeyError:
        return MEMORY_TARGET_UNKNOWN


def apply_memory_correction_prompt_hint(prompt: str, decision: str) -> str:
    """Guardrail sau model: prompt nói rõ quên/xóa hay sửa thì ưu tiên ý đó."""
    if is_memory_readonly_prompt(prompt):
        return MEMORY_CORRECTION_NONE
    if decision == MEMORY_CORRECTION_NONE:
        return decision
    normalized = normalize_correction_prompt_text(prompt)
    has_delete = has_delete_memory_marker(normalized)
    has_correct = has_correct_memory_marker(normalized)
    if has_delete and not has_correct:
        return MEMORY_FORGET_MEMORY
    if has_correct and not has_delete:
        return MEMORY_CORRECT_MEMORY
    return decision


def extract_memory_correction_replacement_from_prompt(prompt: str) -> str:
    """Trích nội dung mới sau marker sửa fact khi model bỏ sót `replacement`."""
    raw = str(prompt).strip()
    if not raw:
        return ""
    patterns = (
        r"\bthay\s+th[eế]\s+b[ằa]ng\b",
        r"\bthay\s+b[ằa]ng\b",
        r"\bthay\s+th[aà]nh\b",
        r"\bth[aà]nh\b",
        r"\breplace\s+with\b",
        r"\bchange\s+to\b",
        r"\bupdate\s+to\b",
    )
    for pattern in patterns:
        matches = list(re.finditer(pattern, raw, flags=re.IGNORECASE))
        if not matches:
            continue
        replacement = raw[matches[0].end() :].strip(" \t\r\n:：-–—\"'")
        replacement = re.sub(r"\s+", " ", replacement).strip()
        if replacement:
            return replacement
    return ""


def is_memory_readonly_prompt(prompt: str) -> bool:
    """Nhận diện câu chỉ đọc/list memory để correction gate không mutate nhầm."""
    normalized = normalize_correction_prompt_text(prompt)
    if has_delete_memory_marker(normalized) or has_correct_memory_marker(normalized):
        return False

    strong_readonly_markers = (
        "biet gi ve",
        "nho gi ve",
        "luu gi ve",
        "what do you know about",
        "what facts",
        "which facts",
        "stored facts",
        "remember about",
    )
    if any(marker in normalized for marker in strong_readonly_markers):
        return True

    memory_refs = (
        "memory",
        "mem",
        "bo nho",
        "fact",
        "facts",
        "ky uc",
        "episode",
        "episodes",
        "nho",
        "luu",
        "remember",
        "stored",
    )
    if not any(marker in normalized for marker in memory_refs):
        return False

    readonly_markers = (
        "fact gi",
        "fact nao",
        "facts gi",
        "facts nao",
        "memory gi",
        "mem gi",
        "bo nho gi",
        "nho gi",
        "luu gi",
        "luu nhung",
        "dang luu",
        "da luu",
        "co luu",
        "co nhung",
        "nhung fact",
        "danh sach",
        "liet ke",
        "list",
        "show",
        "xem",
        "kiem tra",
        "inventory",
        "what",
        "which",
    )
    return any(marker in normalized for marker in readonly_markers)


def has_delete_memory_marker(normalized_prompt: str) -> bool:
    """Dấu hiệu user thật sự muốn xóa/quên memory hiện có."""
    markers = (
        "quen",
        "xoa",
        "delete",
        "remove",
        "forget",
        "bo nho",
    )
    return any(marker in normalized_prompt for marker in markers)


def has_correct_memory_marker(normalized_prompt: str) -> bool:
    """Dấu hiệu user thật sự muốn sửa/cập nhật memory hiện có."""
    markers = (
        "sua",
        "cap nhat",
        "doi",
        "update",
        "fix",
        "correct",
        "replace",
        "change",
        "thay bang",
        "thay thanh",
        "thay the",
    )
    return any(marker in normalized_prompt for marker in markers)


def normalize_correction_prompt_text(value: str) -> str:
    """Bỏ dấu/case và gom khoảng trắng để rule an toàn không lệch tiếng Việt."""
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(char for char in decomposed if not unicodedata.combining(char))
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()
