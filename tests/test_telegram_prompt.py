import os
import unittest
from unittest.mock import patch

from bots.chat_gateway import telegram_message_to_gateway
from bots.telegram_bot import build_telegram_prompt, plan_claude_session


class TelegramPromptTests(unittest.TestCase):
    def test_resume_session_does_not_include_prompt_hook_by_default(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_SESSION_MODE": "auto_resume",
                "CLAUDE_RESUME_COMMAND": "fcc-claude --continue -p",
                "CLAUDE_CONTEXT_LIMIT_PERCENT": "80",
                "TELEGRAM_PROMPT_HOOK": "HOOK",
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
                "TELEGRAM_PROMPT_HOOK": "HOOK",
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
        with patch.dict(os.environ, {"TELEGRAM_PROMPT_HOOK": "HOOK", "CHAT_IDENTITY_ENABLED": "1"}, clear=False):
            prompt = build_telegram_prompt("hello", message, include_prompt_hook=False)

        self.assertNotIn("HOOK", prompt)
        self.assertIn("user_key: telegram:123", prompt)
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


if __name__ == "__main__":
    unittest.main()
