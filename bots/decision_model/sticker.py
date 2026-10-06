"""Decision model cho mood sticker Telegram.

Sticker không cần một agent trả lời dài; Niko chỉ cần Nimble chọn một label mood
nhỏ rồi gateway dùng mood đó để lấy sticker phù hợp trong config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from bots.decision_model.client import (
    ChoiceDecision,
    DecisionModelConfig,
    load_decision_model_config,
    systemone_choice,
)
from niko.config import env_value


NO_STICKER_MOOD = "no_sticker"
QUESTION_NAME = "sticker_mood"
MAX_STICKER_CONTEXT_LENGTH = 1200

STICKER_MOOD_DESCRIPTIONS = {
    "neutral": "A light neutral sticker fits the reply.",
    "warning": "Use for errors, failures, blocked work, or caution.",
    "happy": "Use for thanks, friendly warmth, casual positivity, or relief.",
    "thinking": "Use for questions, uncertainty, analysis, or careful thought.",
    "coding": "Use for coding, Git, APIs, debugging, repo, or tooling work.",
    "tease": "Use only for clearly playful teasing or joking.",
    "hype": "Use for success, done work, shipping, merging, or excitement.",
    "sad": "Use for frustration, tiredness, disappointment, or sympathy.",
}


@dataclass(frozen=True)
class StickerMoodDecision:
    """Mood đã chuẩn hóa để gateway chọn sticker, kèm metadata log."""

    mood: str
    confidence: float | None = None
    label: str = ""
    probabilities: dict[str, float] = field(default_factory=dict)
    provider: str = "ollama_nimble"
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


def decide_sticker_mood(
    user_prompt: str,
    answer: str,
    available_moods: list[str] | tuple[str, ...] | None = None,
    config: DecisionModelConfig | None = None,
) -> StickerMoodDecision:
    """Hỏi Nimble xem reply Telegram có nên thả sticker mood nào không."""
    moods = normalize_available_moods(available_moods or STICKER_MOOD_DESCRIPTIONS.keys())
    decision = systemone_choice(
        state=build_sticker_mood_state(user_prompt, answer),
        question_name=QUESTION_NAME,
        instructions=build_sticker_mood_instructions(),
        criteria=build_sticker_mood_criteria(moods),
        config=config or load_sticker_decision_model_config(),
    )
    mood = normalize_sticker_mood(decision.choice, moods)
    return StickerMoodDecision(
        mood=mood,
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )


def load_sticker_decision_model_config() -> DecisionModelConfig:
    """Dùng cấu hình Ollama chung, cho phép sticker có timeout ngắn hơn."""
    config = load_decision_model_config()
    timeout_raw = env_value("TELEGRAM_STICKER_DECISION_MODEL_TIMEOUT_SECONDS", "").strip()
    if not timeout_raw:
        return config

    try:
        timeout_seconds = max(0.1, float(timeout_raw))
    except ValueError:
        return config

    return DecisionModelConfig(
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=timeout_seconds,
        keep_alive=config.keep_alive,
    )


def build_sticker_mood_state(user_prompt: str, answer: str) -> dict[str, str]:
    """Payload tối thiểu: prompt gốc và câu Niko vừa gửi cho user."""
    return {
        "user_prompt": truncate_sticker_context(user_prompt),
        "answer": truncate_sticker_context(answer),
    }


def build_sticker_mood_instructions() -> str:
    """Luật chọn mood: sticker là phụ họa, không phải kênh trả lời chính."""
    return (
        "Choose whether Niko should send a Telegram sticker after the text reply, "
        "and choose the sticker mood. Prefer no_sticker for serious, sensitive, "
        "plainly informational, or uncertain situations. Choose one mood only when "
        "a small expressive sticker would clearly fit the user's prompt and Niko's reply."
    )


def build_sticker_mood_criteria(available_moods: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Schema choice gửi cho Nimble, luôn có `no_sticker`."""
    criteria = {
        NO_STICKER_MOOD: "Do not send any sticker.",
    }
    for mood in normalize_available_moods(available_moods):
        criteria[mood] = STICKER_MOOD_DESCRIPTIONS.get(mood, f"Use a {mood} sticker when it clearly fits.")
    return criteria


def available_moods_from_config(config: dict[str, Any]) -> list[str]:
    """Lấy mood hợp lệ từ config, ưu tiên thứ tự `mood_priority`."""
    raw_moods = config.get("moods", {})
    if not isinstance(raw_moods, dict):
        return []

    ordered: list[str] = []
    priority = config.get("mood_priority", [])
    if isinstance(priority, list):
        ordered.extend(str(mood) for mood in priority if str(mood) in raw_moods)
    ordered.extend(str(mood) for mood in raw_moods if str(mood) not in ordered)
    return normalize_available_moods(ordered)


def normalize_available_moods(moods: list[str] | tuple[str, ...]) -> list[str]:
    """Chuẩn hóa danh sách mood và bỏ trùng/`no_sticker`."""
    normalized_moods: list[str] = []
    for mood in moods:
        normalized = normalize_mood_text(str(mood))
        if not normalized or normalized == NO_STICKER_MOOD:
            continue
        if normalized not in normalized_moods:
            normalized_moods.append(normalized)
    return normalized_moods


def normalize_sticker_mood(choice: str, available_moods: list[str] | tuple[str, ...]) -> str:
    """Chấp nhận alias nhỏ; mood ngoài config bị coi là lỗi model."""
    normalized = normalize_mood_text(choice)
    aliases = {
        "none": NO_STICKER_MOOD,
        "skip": NO_STICKER_MOOD,
        "no": NO_STICKER_MOOD,
        "no_sticker": NO_STICKER_MOOD,
        "nosticker": NO_STICKER_MOOD,
        "without_sticker": NO_STICKER_MOOD,
    }
    if normalized in aliases:
        return aliases[normalized]

    valid_moods = set(normalize_available_moods(available_moods))
    if normalized in valid_moods:
        return normalized
    raise RuntimeError(f"Ollama sticker decision mood khong hop le: {choice or '(empty)'}")


def normalize_mood_text(value: str) -> str:
    """Đổi label model trả về về dạng snake_case an toàn."""
    normalized = value.strip().lower().strip("`\"'.,:;")
    normalized = re.sub(r"[\s-]+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return normalized


def truncate_sticker_context(value: str, limit: int = MAX_STICKER_CONTEXT_LENGTH) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


def route_meta(decision: ChoiceDecision | StickerMoodDecision) -> dict[str, Any]:
    """Metadata sạch nếu sau này cần đưa sticker decision vào trace."""
    meta: dict[str, Any] = {}
    mood = getattr(decision, "mood", "") or getattr(decision, "choice", "")
    if mood:
        meta["sticker_mood"] = mood
    confidence = getattr(decision, "confidence", None)
    if confidence is not None:
        meta["confidence"] = confidence
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
