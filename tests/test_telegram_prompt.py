import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.chat_gateway import telegram_message_to_gateway
from bots.telegram_bot import (
    STICKER_SET_CACHE,
    build_telegram_prompt,
    ensure_reply_suffix,
    maybe_send_sticker,
    plan_claude_session,
    uncertain_delay_seconds,
)
from bots.sticker_picker import choose_sticker_file_id, detect_sticker_mood


class TelegramPromptTests(unittest.TestCase):
    def test_resume_session_does_not_include_prompt_hook_by_default(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_SESSION_MODE": "auto_resume",
                "CLAUDE_RESUME_COMMAND": "fcc-claude --continue -p",
                "CLAUDE_CONTEXT_LIMIT_PERCENT": "80",
                "TELEGRAM_PROMPT_HOOK_MODE": "new_session",
            },
            clear=False,
        ), patch("bots.telegram_bot.get_context_usage_percent", return_value=20):
            plan = plan_claude_session()

        self.assertEqual(plan.prompt_command, "fcc-claude --continue -p")
        self.assertFalse(plan.include_prompt_hook)

    def test_new_session_includes_prompt_hook_by_default(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_SESSION_MODE": "auto_resume",
                "CLAUDE_NEW_SESSION_PROMPT_COMMAND": "fcc-claude -p",
                "CLAUDE_CONTEXT_LIMIT_PERCENT": "80",
                "TELEGRAM_PROMPT_HOOK_MODE": "new_session",
            },
            clear=False,
        ), patch("bots.telegram_bot.get_context_usage_percent", return_value=90), patch(
            "bots.telegram_bot.prepare_new_claude_session"
        ):
            plan = plan_claude_session()

        self.assertEqual(plan.prompt_command, "fcc-claude -p")
        self.assertTrue(plan.include_prompt_hook)

    def test_build_prompt_can_skip_hook_but_keep_identity(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with patch.dict(os.environ, {"CHAT_IDENTITY_ENABLED": "1"}, clear=False):
            prompt = build_telegram_prompt("hello", message, include_prompt_hook=False)

        self.assertNotIn("Bạn là Niko", prompt)
        self.assertIn("user_key: telegram:123", prompt)
        self.assertIn("Tin nhan nguoi dung:\nhello", prompt)

    def test_build_prompt_loads_hook_from_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            hook_file = Path(temp_dir) / "HOOK.md"
            hook_file.write_text("HOOK FROM FILE", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"TELEGRAM_PROMPT_HOOK_FILE": str(hook_file), "CHAT_IDENTITY_ENABLED": "0"},
                clear=False,
            ):
                prompt = build_telegram_prompt("hello", include_prompt_hook=True)

        self.assertIn("HOOK FROM FILE", prompt)
        self.assertIn("Tin nhan nguoi dung:\nhello", prompt)

    def test_hook_mode_always_keeps_hook_on_resume(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_SESSION_MODE": "auto_resume",
                "TELEGRAM_PROMPT_HOOK_MODE": "always",
                "CLAUDE_CONTEXT_LIMIT_PERCENT": "80",
            },
            clear=False,
        ), patch("bots.telegram_bot.get_context_usage_percent", return_value=20):
            plan = plan_claude_session()

        self.assertTrue(plan.include_prompt_hook)

    def test_reply_suffix_uses_meow_default(self):
        with patch.dict(os.environ, {}, clear=True):
            answer = ensure_reply_suffix("Dạ anh")

        self.assertEqual(answer, "Dạ anh\n\nMeow")

    def test_sticker_picker_detects_warning_mood(self):
        config = {
            "mood_priority": ["warning"],
            "moods": {"warning": {"keywords": ["loi"], "emojis": ["😱"]}},
        }

        self.assertEqual(detect_sticker_mood("Bot bị lỗi rồi anh", config), "warning")

    def test_sticker_picker_chooses_matching_duck_sticker(self):
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": ["👍"]}},
        }
        stickers = [
            {"file_id": "sad-duck", "emoji": "😐"},
            {"file_id": "happy-duck", "emoji": "👍"},
        ]

        sticker = choose_sticker_file_id(stickers, config, "ok anh", chooser=lambda items: items[0])

        self.assertEqual(sticker, "happy-duck")

    def test_sticker_picker_smart_mode_skips_when_no_mood(self):
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": ["👍"]}},
        }

        sticker = choose_sticker_file_id([{"file_id": "duck", "emoji": "👍"}], config, "xin chao")

        self.assertIsNone(sticker)

    def test_maybe_send_sticker_sends_matching_duck_sticker(self):
        STICKER_SET_CACHE.clear()
        calls = []
        config = {
            "set_name": "UtyaDuck",
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": ["👍"]}},
        }

        def fake_telegram_request(token, method, payload):
            calls.append((method, payload))
            if method == "getStickerSet":
                return {"stickers": [{"file_id": "happy-duck", "emoji": "👍"}]}
            return {}

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "1"}, clear=False), patch(
            "bots.telegram_bot.load_effective_sticker_config", return_value=config
        ), patch("bots.telegram_bot.telegram_request", side_effect=fake_telegram_request):
            maybe_send_sticker("token", 123, "ok anh", "Dạ được anh")

        self.assertEqual(calls[0], ("getStickerSet", {"name": "UtyaDuck"}))
        self.assertEqual(calls[1], ("sendSticker", {"chat_id": 123, "sticker": "happy-duck"}))

    def test_uncertain_delay_seconds_is_bounded(self):
        with patch.dict(os.environ, {"TELEGRAM_UNCERTAIN_DELAY_SECONDS": "45"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 30.0)

        with patch.dict(os.environ, {"TELEGRAM_UNCERTAIN_DELAY_SECONDS": "2.5"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 2.5)

if __name__ == "__main__":
    unittest.main()
