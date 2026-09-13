from __future__ import annotations

import json
import random
import unicodedata
from pathlib import Path
from typing import Any, Callable


StickerChooser = Callable[[list[Any]], Any]


def load_sticker_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def detect_sticker_mood(text: str, config: dict[str, Any]) -> str | None:
    normalized_text = normalize_text(text)
    moods = config.get('moods', {})
    for mood in config.get('mood_priority', moods.keys()):
        rule = moods.get(mood, {})
        for keyword in rule.get('keywords', []):
            normalized_keyword = normalize_text(str(keyword))
            if normalized_keyword and normalized_keyword in normalized_text:
                return str(mood)
    return None


def choose_sticker_file_id(
    stickers: list[Any],
    config: dict[str, Any],
    source_text: str,
    chooser: StickerChooser | None = None,
) -> str | None:
    mode = str(config.get('mode', 'smart')).strip().lower()
    if mode in {'off', '0', 'false', 'no'}:
        return None

    mood = detect_sticker_mood(source_text, config)
    if mood is None:
        if mode != 'always':
            return None
        mood = str(config.get('fallback_mood', 'neutral'))

    chooser = chooser or random.choice
    return choose_sticker_for_mood(stickers, config, mood, chooser)


def choose_sticker_for_mood(
    stickers: list[Any],
    config: dict[str, Any],
    mood: str,
    chooser: StickerChooser,
) -> str | None:
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


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize('NFKD', text.casefold())
    return ''.join(char for char in decomposed if not unicodedata.combining(char))
