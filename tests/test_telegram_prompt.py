import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.telegram.bot import (
    STICKER_SET_CACHE,
    format_reply_for_recipient,
    handle_message,
    maybe_send_sticker,
)
from bots.telegram.sticker_picker import choose_sticker_file_id, detect_sticker_mood
from niko.agent import (
    DeepAgentJob,
    FAST_AGENT_TASK_BUSY,
    FAST_AGENT_TASK_FINAL,
    FAST_AGENT_TASK_TRIAGE,
    FAST_AGENT_TASK_WAIT,
    FAST_DECISION_SEND_TO_DEEP,
    NikoAgent,
    build_niko_prompt,
    call_fast_agent,
    deep_agent_command,
    ensure_reply_suffix,
    parse_fast_agent_decision,
    run_cli,
    sanitize_tool_like_answer,
    uncertain_delay_seconds,
)
from niko.agent_router import ROUTE_BUSY_REPLY, ROUTE_FAST_AGENT
from niko.chat_gateway import telegram_message_to_gateway
from niko.config import load_env_files


class TelegramPromptTests(unittest.TestCase):
    def test_group_sticker_without_mention_is_ignored(self):
        message = {
            "sticker": {"file_id": "duck"},
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
        }

        with patch("bots.telegram.bot.send_message") as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        send_message.assert_not_called()

    def test_group_reply_without_mention_is_ignored(self):
        message = {
            "text": "tiep di",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
            "reply_to_message": {"from": {"is_bot": True, "username": "NikoBot"}},
        }

        with patch.dict(os.environ, {"TELEGRAM_GROUP_MODE": "mentions"}, clear=False), patch(
            "bots.telegram.bot.send_message"
        ) as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        send_message.assert_not_called()

    def test_group_id_command_without_mention_returns_identity(self):
        message = {
            "text": "/id",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
        }

        with patch.dict(os.environ, {"TELEGRAM_GROUP_MODE": "mentions"}, clear=False), patch(
            "bots.telegram.bot.send_message"
        ) as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        reply = send_message.call_args.args[2]
        self.assertIn("chat_id: -100", reply)
        self.assertIn("user_key: telegram:123", reply)

    def test_group_id_command_for_other_bot_is_ignored(self):
        message = {
            "text": "/id@OtherBot",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
        }

        with patch.dict(os.environ, {"TELEGRAM_GROUP_MODE": "mentions"}, clear=False), patch(
            "bots.telegram.bot.send_message"
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
                "NIKO_AGENT_MODE": "two_agent",
                "TELEGRAM_GROUP_MODE": "mentions",
                "TELEGRAM_MENTION_REPLIES": "1",
                "TELEGRAM_STICKERS_ENABLED": "0",
            },
            clear=False,
        ), patch("bots.telegram.bot.send_message") as send_message:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        self.assertTrue(send_message.call_args.args[2].startswith("@anhluong "))

    def test_deep_agent_result_is_composed_by_fast_agent(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = NikoAgent()
        delivered = []

        with patch.dict(
            os.environ,
            {"NIKO_FAST_AGENT_COMMAND": "fast -p", "NIKO_REPLY_SUFFIX": "Meow"},
            clear=False,
        ), patch("niko.agent.call_deep_agent", return_value="DEEP RAW"), patch(
            "niko.agent.call_fast_agent", return_value="FAST FINAL"
        ) as fast_agent:
            agent.run_deep_agent_job("456", "phan tich giup anh", message, delivered.append)

        self.assertEqual(delivered, ["FAST FINAL\n\nMeow"])
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_FINAL)
        self.assertEqual(fast_agent.call_args.kwargs["deep_answer"], "DEEP RAW")

    def test_busy_deep_job_uses_fast_agent_and_tracks_followup(self):
        message = telegram_message_to_gateway(
            {
                "text": "them thong tin",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = NikoAgent()
        conversation_id = agent.conversation_id_for(message)
        active_job = DeepAgentJob(conversation_id, message.user.key, "original prompt", time.time())
        agent.deep_jobs[conversation_id] = active_job
        delivered = []

        with patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            },
            clear=False,
        ), patch("niko.agent.call_fast_agent", return_value="FAST BUSY") as fast_agent:
            route = agent.handle_message("them thong tin", message, delivered.append)

        self.assertEqual(route, ROUTE_BUSY_REPLY)
        self.assertEqual(delivered, ["FAST BUSY\n\nMeow"])
        self.assertEqual(active_job.followups, ["them thong tin"])
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_BUSY)
        self.assertIs(fast_agent.call_args.kwargs["active_job"], active_job)

    def test_fast_agent_decision_parses_json_fence_and_suffix(self):
        raw_answer = (
            "```json\n"
            "{\"route\":\"send_to_deep\",\"reply\":\"Da anh doi em chut\"}\n"
            "```\n\n"
            "Meow"
        )

        with patch.dict(os.environ, {"NIKO_REPLY_SUFFIX": "Meow"}, clear=True):
            decision = parse_fast_agent_decision(raw_answer)

        self.assertEqual(decision.route, FAST_DECISION_SEND_TO_DEEP)
        self.assertEqual(decision.reply, "Da anh doi em chut")

    def test_fast_triage_prompt_skips_hook(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            hook_file = Path(temp_dir) / "HOOK.md"
            hook_file.write_text("HOOK FROM FILE", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "NIKO_FAST_AGENT_COMMAND": "fast -p",
                    "NIKO_PROMPT_HOOK_FILE": str(hook_file),
                    "CHAT_IDENTITY_ENABLED": "0",
                },
                clear=True,
            ), patch(
                "niko.agent.run_cli",
                return_value='{\"route\":\"reply_now\",\"reply\":\"Da anh\"}',
            ) as run:
                call_fast_agent("hello", message, task=FAST_AGENT_TASK_TRIAGE)

        prompt = run.call_args.args[1]
        self.assertNotIn("HOOK FROM FILE", prompt)
        self.assertIn("Nhiem vu: phan loai", prompt)

    def test_fast_triage_reply_now_does_not_start_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "thoi tiet dep khong",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = NikoAgent()
        delivered = []

        with patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            },
            clear=True,
        ), patch(
            "niko.agent.call_fast_agent",
            return_value='{\"route\":\"reply_now\",\"reply\":\"Da em tra loi nhanh duoc anh.\"}',
        ) as fast_agent, patch.object(
            agent,
            "start_deep_agent_job",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("thoi tiet dep khong", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["Da em tra loi nhanh duoc anh.\n\nMeow"])
        start_deep.assert_not_called()
        fast_agent.assert_called_once()
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_TRIAGE)

    def test_fast_triage_can_handoff_to_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = NikoAgent()
        delivered = []

        with patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            },
            clear=True,
        ), patch(
            "niko.agent.call_fast_agent",
            return_value='{\"route\":\"send_to_deep\",\"reply\":\"Da anh doi em chut\"}',
        ) as fast_agent, patch.object(
            agent,
            "start_deep_agent_job",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["Da anh doi em chut\n\nMeow"])
        start_deep.assert_called_once()
        fast_agent.assert_called_once()
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_TRIAGE)

    def test_invalid_fast_triage_falls_back_to_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = NikoAgent()
        delivered = []

        with patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            },
            clear=True,
        ), patch(
            "niko.agent.call_fast_agent",
            side_effect=["khong phai json", "FAST WAIT"],
        ) as fast_agent, patch.object(
            agent,
            "start_deep_agent_job",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["FAST WAIT\n\nMeow"])
        start_deep.assert_called_once()
        self.assertEqual(
            [call.kwargs["task"] for call in fast_agent.call_args_list],
            [FAST_AGENT_TASK_TRIAGE, FAST_AGENT_TASK_WAIT],
        )

    def test_group_reply_can_use_html_mention_when_username_missing(self):
        message = telegram_message_to_gateway(
            {
                "text": "@NikoBot alo",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": -100, "type": "supergroup", "title": "Niko Test"},
            },
            {"telegram:123": "Con,telegram:456=Con cu"},
        )

        text, parse_mode = format_reply_for_recipient("Da anh", message)

        self.assertEqual(parse_mode, "HTML")
        self.assertIn('<a href="tg://user?id=123">Luong</a>', text)
        self.assertNotIn("telegram:456", text)
        self.assertIn("Da anh", text)

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
            prompt = build_niko_prompt("hello", message, include_prompt_hook=False)

        self.assertNotIn("Ban la Niko", prompt)
        self.assertIn("user_key: telegram:123", prompt)
        self.assertIn("Tin nhan nguoi dung:\nhello", prompt)

    def test_build_prompt_loads_hook_from_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            hook_file = Path(temp_dir) / "HOOK.md"
            hook_file.write_text("HOOK FROM FILE", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"NIKO_PROMPT_HOOK_FILE": str(hook_file), "CHAT_IDENTITY_ENABLED": "0"},
                clear=False,
            ):
                prompt = build_niko_prompt("hello", include_prompt_hook=True)

        self.assertIn("HOOK FROM FILE", prompt)
        self.assertIn("Tin nhan nguoi dung:\nhello", prompt)

    def test_load_env_files_reads_root_niko_and_telegram_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "CLAUDE_TIMEOUT_SECONDS=7",
                        "NIKO_AGENT_MODE=single",
                        "TELEGRAM_GROUP_MODE=all",
                        "PRESERVE_ME=root",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            niko_env_dir = root / "niko"
            niko_env_dir.mkdir(parents=True)
            (niko_env_dir / ".env").write_text(
                "\n".join(
                    [
                        "NIKO_AGENT_MODE=two_agent",
                        "NIKO_REPLY_SUFFIX=Meow",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            telegram_env_dir = root / "bots" / "telegram"
            telegram_env_dir.mkdir(parents=True)
            (telegram_env_dir / ".env").write_text(
                "\n".join(
                    [
                        "TELEGRAM_GROUP_MODE=mentions",
                        "TELEGRAM_BOT_TOKEN=token",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"PRESERVE_ME": "shell"}, clear=True), patch(
                "niko.config.repo_root", return_value=root
            ):
                load_env_files("telegram")
                self.assertEqual(os.environ["CLAUDE_TIMEOUT_SECONDS"], "7")
                self.assertEqual(os.environ["NIKO_AGENT_MODE"], "two_agent")
                self.assertEqual(os.environ["NIKO_REPLY_SUFFIX"], "Meow")
                self.assertEqual(os.environ["TELEGRAM_GROUP_MODE"], "mentions")
                self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "token")
                self.assertEqual(os.environ["PRESERVE_ME"], "shell")

    def test_run_cli_uses_configured_workdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "claude_sandbox"
            completed = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
            with patch.dict(os.environ, {"CLAUDE_WORKDIR": str(workdir)}, clear=False), patch(
                "niko.agent.subprocess.run", return_value=completed
            ) as run:
                self.assertEqual(run_cli("fcc-claude -p", "hello"), "OK")

            self.assertEqual(run.call_args.kwargs["cwd"], workdir)
            self.assertTrue(workdir.exists())

    def test_tool_like_answer_is_replaced_before_suffix(self):
        raw_answer = (
            "{\n"
            "  \"tool\": \"read\",\n"
            "  \"arguments\": {\n"
            "    \"path\": \"./.git/objects/info/packs\"\n"
            "  }\n"
            "}\n"
            "</tool>Meow"
        )

        with patch.dict(
            os.environ,
            {"NIKO_TOOL_UNAVAILABLE_REPLY": "NO_TOOL", "NIKO_REPLY_SUFFIX": "Meow"},
            clear=False,
        ):
            answer = ensure_reply_suffix(raw_answer)

        self.assertIn("NO_TOOL", answer)
        self.assertNotIn('"tool"', answer)
        self.assertTrue(answer.endswith("Meow"))

    def test_normal_json_answer_is_not_replaced(self):
        raw_answer = '{"answer": "OK"}'

        self.assertEqual(sanitize_tool_like_answer(raw_answer), raw_answer)

    def test_reply_suffix_uses_meow_default(self):
        with patch.dict(os.environ, {}, clear=True):
            answer = ensure_reply_suffix("Da anh")

        self.assertEqual(answer, "Da anh\n\nMeow")

    def test_sticker_picker_detects_warning_mood(self):
        config = {
            "mood_priority": ["warning"],
            "moods": {"warning": {"keywords": ["loi"], "emojis": ["\U0001f631"]}},
        }

        self.assertEqual(detect_sticker_mood("Bot bi loi roi anh", config), "warning")

    def test_sticker_picker_chooses_matching_duck_sticker(self):
        thumbs_up = "\U0001f44d"
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": [thumbs_up]}},
        }
        stickers = [
            {"file_id": "sad-duck", "emoji": "\U0001f610"},
            {"file_id": "happy-duck", "emoji": thumbs_up},
        ]

        sticker = choose_sticker_file_id(stickers, config, "ok anh", chooser=lambda items: items[0])

        self.assertEqual(sticker, "happy-duck")

    def test_sticker_picker_smart_mode_skips_when_no_mood(self):
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": ["\U0001f44d"]}},
        }

        sticker = choose_sticker_file_id([{"file_id": "duck", "emoji": "\U0001f44d"}], config, "xin chao")

        self.assertIsNone(sticker)

    def test_maybe_send_sticker_sends_matching_duck_sticker(self):
        STICKER_SET_CACHE.clear()
        calls = []
        thumbs_up = "\U0001f44d"
        config = {
            "set_name": "UtyaDuck",
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"keywords": ["ok"], "emojis": [thumbs_up]}},
        }

        def fake_telegram_request(token, method, payload):
            calls.append((method, payload))
            if method == "getStickerSet":
                return {"stickers": [{"file_id": "happy-duck", "emoji": thumbs_up}]}
            return {}

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "1"}, clear=False), patch(
            "bots.telegram.bot.load_effective_sticker_config", return_value=config
        ), patch("bots.telegram.bot.telegram_request", side_effect=fake_telegram_request):
            maybe_send_sticker("token", 123, "ok anh", "Da duoc anh")

        self.assertEqual(calls[0], ("getStickerSet", {"name": "UtyaDuck"}))
        self.assertEqual(calls[1], ("sendSticker", {"chat_id": 123, "sticker": "happy-duck"}))

    def test_uncertain_delay_seconds_is_bounded(self):
        with patch.dict(os.environ, {"NIKO_UNCERTAIN_DELAY_SECONDS": "45"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 30.0)

        with patch.dict(os.environ, {"NIKO_UNCERTAIN_DELAY_SECONDS": "2.5"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 2.5)


if __name__ == "__main__":
    unittest.main()
