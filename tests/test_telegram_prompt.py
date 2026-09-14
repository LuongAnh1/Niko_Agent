import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.chat_gateway import telegram_message_to_gateway
from bots.telegram_bot import (
    STICKER_SET_CACHE,
    build_telegram_prompt,
    deep_agent_command,
    ensure_reply_suffix,
    format_reply_for_recipient,
    handle_message,
    maybe_send_sticker,
    run_cli,
    sanitize_tool_like_answer,
    uncertain_delay_seconds,
)
from bots.sticker_picker import choose_sticker_file_id, detect_sticker_mood


class TelegramPromptTests(unittest.TestCase):

    def test_group_sticker_without_mention_is_ignored(self):
        message = {
            "sticker": {"file_id": "duck"},
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
        }

        with patch("bots.telegram_bot.send_message") as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        send_message.assert_not_called()

    def test_group_reply_without_mention_is_ignored(self):
        message = {
            "text": "tiếp đi",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
            "reply_to_message": {"from": {"is_bot": True, "username": "NikoBot"}},
        }

        with patch.dict(os.environ, {"TELEGRAM_GROUP_MODE": "mentions"}, clear=False), patch(
            "bots.telegram_bot.send_message"
        ) as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        send_message.assert_not_called()

    def test_group_mention_gets_reply_with_user_mention(self):
        message = {
            "text": "@NikoBot alo",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
        }

        with patch.dict(
            os.environ,
            {
                "TELEGRAM_AGENT_MODE": "two_agent",
                "TELEGRAM_GROUP_MODE": "mentions",
                "TELEGRAM_MENTION_REPLIES": "1",
                "TELEGRAM_STICKERS_ENABLED": "0",
            },
            clear=False,
        ), patch("bots.telegram_bot.send_message") as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        self.assertTrue(send_message.call_args.args[2].startswith("@anhluong "))

    def test_group_reply_can_use_html_mention_when_username_missing(self):
        message = telegram_message_to_gateway(
            {
                "text": "@NikoBot alo",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
            },
            {"telegram:123": "Con,telegram:456=Con cu"},
        )

        text, parse_mode = format_reply_for_recipient("Dạ anh", message)

        self.assertEqual(parse_mode, "HTML")
        self.assertIn('<a href="tg://user?id=123">Luong</a>', text)
        self.assertNotIn("telegram:456", text)
        self.assertIn("Dạ anh", text)

    def test_deep_agent_command_defaults_to_claude_command(self):
        with patch.dict(os.environ, {"CLAUDE_CLI_COMMAND": "fcc-claude -p"}, clear=True):
            self.assertEqual(deep_agent_command(), "fcc-claude -p")

    def test_deep_agent_command_can_override_default(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_CLI_COMMAND": "fcc-claude -p",
                "CLAUDE_DEEP_AGENT_COMMAND": "fcc-claude --model opus[1m] -p",
            },
            clear=True,
        ):
            self.assertEqual(deep_agent_command(), "fcc-claude --model opus[1m] -p")

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


    def test_run_cli_uses_configured_workdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "claude_sandbox"
            completed = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
            with patch.dict(os.environ, {"CLAUDE_WORKDIR": str(workdir)}, clear=False), patch(
                "bots.telegram_bot.subprocess.run", return_value=completed
            ) as run:
                self.assertEqual(run_cli("fcc-claude -p", "hello"), "OK")

            self.assertEqual(run.call_args.kwargs["cwd"], workdir)
            self.assertTrue(workdir.exists())


    def test_tool_like_answer_is_replaced_before_suffix(self):
        raw_answer = '''{
  "tool": "read",
  "arguments": {
    "path": "./.git/objects/info/packs"
  }
}
</tool>Meow'''

        answer = ensure_reply_suffix(raw_answer)

        self.assertIn("không có quyền tự đọc file", answer)
        self.assertNotIn('"tool"', answer)
        self.assertTrue(answer.endswith("Meow"))

    def test_normal_json_answer_is_not_replaced(self):
        raw_answer = '{"answer": "OK"}'

        self.assertEqual(sanitize_tool_like_answer(raw_answer), raw_answer)

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
