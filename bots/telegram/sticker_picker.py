"""Chọn sticker Telegram theo mood đã được quyết định.

Sticker là lớp trang trí phía gateway, không ảnh hưởng route hay memory. File này
chỉ biết mapping JSON và sticker set Telegram; decision model mới là nơi chọn mood.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Callable


StickerChooser = Callable[[list[Any]], Any]
NO_STICKER_MOOD = 'no_sticker'


def load_sticker_config(path: Path) -> dict[str, Any]:
    """Đọc mapping mood/sticker từ JSON trong repo."""
    return json.loads(path.read_text(encoding='utf-8'))


def choose_sticker_file_id(
    stickers: list[Any],
    config: dict[str, Any],
    mood: str,
    chooser: StickerChooser | None = None,
) -> str | None:
    """Trả về file_id cho mood đã chọn, hoặc None nếu không nên gửi."""
    mode = str(config.get('mode', 'smart')).strip().lower()
    if mode in {'off', '0', 'false', 'no'}:
        return None

    normalized_mood = str(mood).strip().lower()
    if not normalized_mood or normalized_mood == NO_STICKER_MOOD:
        return None

    moods = config.get('moods', {})
    if normalized_mood not in moods:
        return None

    chooser = chooser or random.choice
    return choose_sticker_for_mood(stickers, config, normalized_mood, chooser)


def choose_sticker_for_mood(
    stickers: list[Any],
    config: dict[str, Any],
    mood: str,
    chooser: StickerChooser,
) -> str | None:
    """Ưu tiên file_id khai báo tay, rồi fallback sang emoji của sticker set."""
    moods = config.get('moods', {})
    rule = moods.get(mood, {})

    configured_file_ids = [str(file_id) for file_id in rule.get('file_ids', []) if file_id]
    if configured_file_ids:
        return chooser(configured_file_ids)

    candidates = stickers_matching_emojis(stickers, rule.get('emojis', []))
    if not candidates and mood != config.get('fallback_mood'):
        fallback_rule = moods.get(config.get('fallback_mood', 'neutral'), {})
        candidates = stickers_matching_emojis(stickers, fallback_rule.get('emojis', []))
    if not candidates:
        candidates = [sticker for sticker in stickers if extract_sticker_file_id(sticker)]
    if not candidates:
        return None

    return extract_sticker_file_id(chooser(candidates))


def stickers_matching_emojis(stickers: list[Any], emojis: list[str]) -> list[Any]:
    wanted = {str(emoji).strip() for emoji in emojis if str(emoji).strip()}
    if not wanted:
        return []
    return [sticker for sticker in stickers if sticker_emoji(sticker) in wanted]


def sticker_emoji(sticker: Any) -> str:
    if isinstance(sticker, dict):
        return str(sticker.get('emoji', '')).strip()
    return ''


def extract_sticker_file_id(sticker: Any) -> str | None:
    if isinstance(sticker, str):
        return sticker.strip() or None
    if isinstance(sticker, dict):
        file_id = str(sticker.get('file_id', '')).strip()
        return file_id or None
    return None
