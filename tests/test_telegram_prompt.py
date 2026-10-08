import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.telegram.bot import (
    STICKER_SET_CACHE,
    TelegramError,
    decision_triage_status_line,
    format_sticker_decision_log,
    format_reply_for_recipient,
    handle_message,
    is_getupdates_conflict,
    maybe_send_sticker,
    prepare_long_polling,
    send_chat_action,
    sticker_decision_status_line,
    telegram_request,
)
from bots.decision_model.sticker import StickerMoodDecision
from bots.decision_model.jira import JIRA_ASK_FOR_ISSUE_KEY, JIRA_USE_TOOL, JiraGateDecision
from bots.telegram.sticker_picker import choose_sticker_file_id
from niko.graphs.chat_reply import ChatReplyGraph, DeepAgentJob
from niko.graphs.chat_reply.graph import format_fast_triage_log
from niko.graphs.jira_issue import (
    ROUTE_JIRA_ISSUE_DEEP_AGENT,
    ROUTE_JIRA_ISSUE_KEY_REQUIRED,
    ROUTE_JIRA_ISSUE_NOT_FOUND,
)
from niko.graphs.chat_reply.prompts import (
    FAST_AGENT_TASK_FINAL,
    FAST_AGENT_TASK_REPLY,
    FAST_AGENT_TASK_TRIAGE,
    FAST_DECISION_SEND_TO_DEEP,
    FastAgentDecision,
    build_deep_busy_reply,
    build_deep_wait_reply,
    call_fast_agent,
    ensure_reply_suffix,
    parse_fast_agent_decision,
    sanitize_tool_like_answer,
    uncertain_delay_seconds,
)
from niko.graphs.chat_reply.router import ROUTE_BUSY_REPLY, ROUTE_FAST_AGENT
from niko.harness.trace import TraceLogger
from niko.memory.store import MemoryStore
from niko.runtime import build_niko_prompt, deep_agent_command, run_cli
from niko.chat_gateway import telegram_message_to_gateway
from niko.config import load_env_files


class TelegramPromptTests(unittest.TestCase):
    def setUp(self):
        self._state_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self._state_dir.name)
        self.runtime_config_file = self.state_path / "config.json"
        self.memory_store = MemoryStore(self.state_path / "memory.sqlite3")
        self.trace_logger = TraceLogger(self.state_path / "traces", enabled=True)
        self._env_patch = patch.dict(
            os.environ,
            {
                "NIKO_STATE_DIR": str(self.state_path),
                "NIKO_RUNTIME_CONFIG_FILE": str(self.runtime_config_file),
                "NIKO_MEMORY_WRITE_ENABLED": "0",
            },
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._state_dir.cleanup()

    def make_agent(self) -> ChatReplyGraph:
        return ChatReplyGraph(memory_store=self.memory_store, trace_logger=self.trace_logger)

    def isolated_env(self, values: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "NIKO_STATE_DIR": str(self.state_path),
            "NIKO_RUNTIME_CONFIG_FILE": str(self.runtime_config_file),
            "NIKO_MEMORY_WRITE_ENABLED": "0",
        }
        env.update(values or {})
        return env

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

    def test_telegram_request_converts_timeout_to_telegram_error(self):
        with patch("bots.telegram.bot.urlopen", side_effect=TimeoutError("slow network")):
            with self.assertRaises(TelegramError):
                telegram_request("token", "getMe", {}, timeout_seconds=5)

    def test_getupdates_conflict_detection(self):
        error = RuntimeError(
            'Telegram HTTP 409: {"description":"Conflict: terminated by other getUpdates request"}'
        )

        self.assertTrue(is_getupdates_conflict(error))
        self.assertFalse(is_getupdates_conflict(RuntimeError("Telegram HTTP 500: server error")))

    def test_prepare_long_polling_warns_instead_of_crashing_after_timeout(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_STARTUP_RETRIES": "1", "TELEGRAM_STARTUP_RETRY_DELAY_SECONDS": "0"},
            clear=False,
        ), patch(
            "bots.telegram.bot.telegram_request",
            side_effect=TelegramError("timeout"),
        ) as request, patch("bots.telegram.bot.time.sleep") as sleep:
            prepare_long_polling("token")

        self.assertEqual(request.call_count, 2)
        sleep.assert_not_called()

    def test_send_chat_action_uses_short_timeout(self):
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS": "2"}, clear=False), patch(
            "bots.telegram.bot.telegram_request"
        ) as request:
            send_chat_action("token", 123)

        request.assert_called_once_with(
            "token",
            "sendChatAction",
            {"chat_id": 123, "action": "typing"},
            timeout_seconds=2,
        )

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

    def test_telegram_message_uses_gateway_runner(self):
        message = {
            "text": "alo",
            "from": {"id": 123, "username": "anhluong", "first_name": "Luong"},
            "chat": {"id": 456, "type": "private"},
        }

        class FakeGatewayRunner:
            def __init__(self) -> None:
                self.calls = []

            def handle_message(self, prompt, gateway_message, deliver_reply, notify_working=None):
                self.calls.append((prompt, gateway_message, deliver_reply, notify_working))
                deliver_reply("runner reply")
                return "runner_route"

        runner = FakeGatewayRunner()

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "0"}, clear=False), patch(
            "bots.telegram.bot.GATEWAY_RUNNER", runner
        ), patch("bots.telegram.bot.send_message") as send_message, patch(
            "bots.telegram.bot.maybe_send_sticker_async"
        ) as sticker:
            handle_message("token", message, set(), set(), {}, "NikoBot")

        self.assertEqual(len(runner.calls), 1)
        prompt, gateway_message, deliver_reply, notify_working = runner.calls[0]
        self.assertEqual(prompt, "alo")
        self.assertEqual(gateway_message.chat_id, "456")
        self.assertIsNotNone(deliver_reply)
        self.assertIsNotNone(notify_working)
        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args[2], "runner reply")
        sticker.assert_called_once()

    def test_deep_agent_result_is_composed_by_fast_agent(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            {"NIKO_FAST_AGENT_COMMAND": "fast -p", "NIKO_REPLY_SUFFIX": "Meow"},
            clear=False,
        ), patch("niko.runtime.call_deep_agent", return_value="DEEP RAW"), patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent", return_value="FAST FINAL"
        ) as fast_agent:
            agent.run_deep_agent_job("456", "phan tich giup anh", message, delivered.append)

        self.assertEqual(delivered, ["FAST FINAL\n\nMeow"])
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_FINAL)
        self.assertEqual(fast_agent.call_args.kwargs["deep_answer"], "DEEP RAW")

    def test_deep_agent_result_is_logged_as_episode(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        delivered = []

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            agent = ChatReplyGraph(memory_store=store, trace_logger=trace_logger)

            with patch.dict(
                os.environ,
                {"NIKO_FAST_AGENT_COMMAND": "fast -p", "NIKO_REPLY_SUFFIX": "Meow", "NIKO_MEMORY_WRITE_ENABLED": "1"},
                clear=False,
            ), patch("niko.runtime.call_deep_agent", return_value="DEEP RAW"), patch(
                "niko.graphs.chat_reply.prompts.call_fast_agent", return_value="FAST FINAL"
            ):
                agent.run_deep_agent_job("456", "phan tich giup anh", message, delivered.append)

            snapshot = store.snapshot()

        self.assertEqual(delivered, ["FAST FINAL\n\nMeow"])
        self.assertEqual(snapshot["counts"]["episodes"], 1)
        self.assertEqual(snapshot["counts"]["chat_log"], 1)
        self.assertIn("phan tich giup anh", snapshot["episodes"][0]["summary"])

    def test_deep_agent_delivery_error_does_not_generate_contradictory_error_reply(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            agent = ChatReplyGraph(memory_store=store, trace_logger=trace_logger)

            def failing_delivery(_answer: str) -> None:
                raise RuntimeError("Telegram request timeout o method sendMessage.")

            with patch.dict(
                os.environ,
                {"NIKO_FAST_AGENT_COMMAND": "fast -p", "NIKO_REPLY_SUFFIX": "Meow", "NIKO_MEMORY_WRITE_ENABLED": "1"},
                clear=False,
            ), patch("niko.runtime.call_deep_agent", return_value="DEEP RAW"), patch(
                "niko.graphs.chat_reply.prompts.call_fast_agent", return_value="FAST FINAL"
            ) as fast_agent:
                agent.run_deep_agent_job("456", "phan tich giup anh", message, failing_delivery)

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        self.assertEqual(snapshot["counts"]["chat_log"], 1)
        self.assertEqual(snapshot["chat_log"][0]["content"], "FAST FINAL\n\nMeow")
        self.assertEqual(fast_agent.call_count, 1)
        self.assertTrue(any(event.get("kind") == "reply_delivery_error" for event in events))

    def test_busy_deep_job_uses_deterministic_reply_and_tracks_followup(self):
        message = telegram_message_to_gateway(
            {
                "text": "them thong tin",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
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
        ), patch("niko.graphs.chat_reply.prompts.call_fast_agent") as fast_agent:
            route = agent.handle_message("them thong tin", message, delivered.append)

        self.assertEqual(route, ROUTE_BUSY_REPLY)
        self.assertEqual(delivered, [ensure_reply_suffix(build_deep_busy_reply(active_job))])
        self.assertEqual(active_job.followups, ["them thong tin"])
        fast_agent.assert_not_called()

    def test_fast_agent_decision_parses_json_fence_and_suffix(self):
        raw_answer = (
            "```json\n"
            "{\"route\":\"send_to_deep\",\"reply\":\"Da anh doi em chut\"}\n"
            "```\n\n"
            "Meow"
        )

        with patch.dict(os.environ, self.isolated_env({"NIKO_REPLY_SUFFIX": "Meow"}), clear=True):
            decision = parse_fast_agent_decision(raw_answer)

        self.assertEqual(decision.route, FAST_DECISION_SEND_TO_DEEP)
        self.assertEqual(decision.reply, "Da anh doi em chut")
        self.assertEqual(decision.provider, "legacy_fast_agent")

    def test_fast_triage_terminal_log_includes_provider_model_and_label(self):
        decision = FastAgentDecision(
            route=FAST_DECISION_SEND_TO_DEEP,
            provider="ollama_nimble",
            model="nimble",
            confidence=0.8754,
            label="send_to_deep",
            probabilities={"reply_now": 0.1246, "send_to_deep": 0.8754},
        )

        log_line = format_fast_triage_log(decision)

        self.assertIn("provider=ollama_nimble", log_line)
        self.assertIn("model=nimble", log_line)
        self.assertIn("route=send_to_deep", log_line)
        self.assertIn("label=send_to_deep", log_line)
        self.assertIn("confidence=0.875", log_line)

    def test_telegram_startup_log_mentions_ollama_decision_model(self):
        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_DECISION_MODEL_ENABLED": "1",
                "NIKO_DECISION_MODEL_BASE_URL": "http://localhost:11434",
                "NIKO_DECISION_MODEL_NAME": "nimble",
                "NIKO_DECISION_MODEL_KEEP_ALIVE": "-1",
            }),
            clear=True,
        ):
            line = decision_triage_status_line()

        self.assertIn("Ollama local enabled", line)
        self.assertIn("model=nimble", line)
        self.assertIn("keep_alive=-1", line)

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
                self.isolated_env({
                    "NIKO_FAST_AGENT_COMMAND": "fast -p",
                    "NIKO_PROMPT_HOOK_FILE": str(hook_file),
                    "CHAT_IDENTITY_ENABLED": "0",
                }),
                clear=True,
            ), patch(
                "niko.runtime.run_cli",
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
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent",
            return_value='{\"route\":\"reply_now\",\"reply\":\"Da em tra loi nhanh duoc anh.\"}',
        ) as fast_agent, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("thoi tiet dep khong", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["Da em tra loi nhanh duoc anh.\n\nMeow"])
        start_deep.assert_not_called()
        fast_agent.assert_called_once()
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_TRIAGE)

    def test_decision_model_reply_now_uses_fast_reply_model(self):
        message = telegram_message_to_gateway(
            {
                "text": "thoi tiet dep khong",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_DECISION_MODEL_ENABLED": "1",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_decision_model",
            return_value=FastAgentDecision(
                route="reply_now",
                provider="ollama_nimble",
                confidence=0.9,
                label="reply_now",
            ),
        ) as decision_model, patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent",
            return_value="Da em tra loi nhanh duoc anh.",
        ) as fast_agent, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("thoi tiet dep khong", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["Da em tra loi nhanh duoc anh.\n\nMeow"])
        start_deep.assert_not_called()
        decision_model.assert_called_once()
        fast_agent.assert_called_once()
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_REPLY)

    def test_decision_model_can_handoff_to_deep_without_fable(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_DECISION_MODEL_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_decision_model",
            return_value=FastAgentDecision(route="send_to_deep", provider="ollama_nimble"),
        ) as decision_model, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, [ensure_reply_suffix(build_deep_wait_reply())])
        start_deep.assert_called_once()
        decision_model.assert_called_once()

    def test_fast_triage_does_not_wait_for_slow_notify_working(self):
        message = telegram_message_to_gateway(
            {
                "text": "thoi tiet dep khong",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []
        notify_started = threading.Event()
        notify_release = threading.Event()

        def slow_notify():
            notify_started.set()
            notify_release.wait(timeout=1)

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent",
            return_value='{\"route\":\"reply_now\",\"reply\":\"Da em tra loi nhanh duoc anh.\"}',
        ):
            started_at = time.perf_counter()
            route = agent.handle_message("thoi tiet dep khong", message, delivered.append, slow_notify)
            elapsed = time.perf_counter() - started_at
            notify_release.set()

        self.assertTrue(notify_started.wait(timeout=1))
        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(delivered, ["Da em tra loi nhanh duoc anh.\n\nMeow"])

    def test_fast_triage_can_handoff_to_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent",
            return_value='{\"route\":\"send_to_deep\",\"reply\":\"Da anh doi em chut\"}',
        ) as fast_agent, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, ["Da anh doi em chut\n\nMeow"])
        start_deep.assert_called_once()
        fast_agent.assert_called_once()
        self.assertEqual(fast_agent.call_args.kwargs["task"], FAST_AGENT_TASK_TRIAGE)

    def test_deep_handoff_delivers_wait_reply_before_starting_thread(self):
        message = telegram_message_to_gateway(
            {
                "text": "can phan tich giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        def assert_wait_already_delivered(*_args, **_kwargs):
            self.assertEqual(delivered, ["Da anh doi em chut\n\nMeow"])

        with patch.dict(
            os.environ,
            {"NIKO_REPLY_SUFFIX": "Meow"},
            clear=False,
        ), patch.object(
            agent,
            "_start_deep_agent_thread",
            side_effect=assert_wait_already_delivered,
        ) as start_deep:
            agent.handoff_to_deep_agent(
                "456",
                "can phan tich giup anh",
                message,
                delivered.append,
                wait_reply="Da anh doi em chut",
                trace_id="trace-1",
            )

        self.assertEqual(delivered, ["Da anh doi em chut\n\nMeow"])
        start_deep.assert_called_once()

    def test_invalid_fast_triage_falls_back_to_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_FAST_AGENT_COMMAND": "fast -p",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_fast_agent",
            side_effect=["khong phai json"],
        ) as fast_agent, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, [ensure_reply_suffix(build_deep_wait_reply())])
        start_deep.assert_called_once()
        self.assertEqual(
            [call.kwargs["task"] for call in fast_agent.call_args_list],
            [FAST_AGENT_TASK_TRIAGE],
        )

    def test_decision_model_error_falls_back_to_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "nen lam cach nao day anh nhi",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_DECISION_MODEL_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.chat_reply.prompts.call_decision_model",
            side_effect=RuntimeError("ollama down"),
        ) as decision_model, patch.object(
            agent,
            "_start_deep_agent_thread",
            return_value=True,
        ) as start_deep:
            route = agent.handle_message("nen lam cach nao day anh nhi", message, delivered.append)

        self.assertEqual(route, ROUTE_FAST_AGENT)
        self.assertEqual(delivered, [ensure_reply_suffix(build_deep_wait_reply())])
        start_deep.assert_called_once()
        decision_model.assert_called_once()

    def test_jira_issue_prompt_fetches_context_before_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich NIKO-101 giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_JIRA_TOOLS_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch.object(agent, "_start_deep_agent_thread", return_value=True) as start_deep:
            route = agent.handle_message("phan tich NIKO-101 giup anh", message, delivered.append)

        job = agent.get_active_deep_job("456")
        self.assertEqual(route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertEqual(delivered, ["Dạ anh đợi em chút, em đã lấy context Jira của NIKO-101 rồi, giờ em phân tích bằng Deep.\n\nMeow"])
        self.assertIsNotNone(job)
        self.assertIn("Jira issue context", job.deep_context)
        self.assertIn("NIKO-101", job.deep_context)
        start_deep.assert_called_once()

    def test_jira_issue_prompt_uses_old_route_when_disabled(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich NIKO-101 giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_JIRA_TOOLS_ENABLED": "0",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch.object(agent, "_start_deep_agent_thread", return_value=True) as start_deep:
            route = agent.handle_message("phan tich NIKO-101 giup anh", message, delivered.append)

        job = agent.get_active_deep_job("456")
        self.assertNotEqual(route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertEqual(delivered, [ensure_reply_suffix(build_deep_wait_reply())])
        self.assertIsNotNone(job)
        self.assertEqual(job.deep_context, "")
        start_deep.assert_called_once()

    def test_jira_issue_missing_fixture_reply_does_not_start_deep(self):
        message = telegram_message_to_gateway(
            {
                "text": "phan tich NIKO-404 giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_JIRA_TOOLS_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch.object(agent, "_start_deep_agent_thread", return_value=True) as start_deep:
            route = agent.handle_message("phan tich NIKO-404 giup anh", message, delivered.append)

        self.assertEqual(route, ROUTE_JIRA_ISSUE_NOT_FOUND)
        self.assertIn("chưa có dữ liệu Jira fixture", delivered[0])
        self.assertTrue(delivered[0].endswith("Meow"))
        self.assertIsNone(agent.get_active_deep_job("456"))
        start_deep.assert_not_called()

    def test_jira_decision_gate_can_use_recent_issue_key_for_followup(self):
        message = telegram_message_to_gateway(
            {
                "text": "xem ticket vua nay giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        agent.memory_store.log_chat("456", "user", "phan tich NIKO-101 giup anh", source="test")
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_JIRA_TOOLS_ENABLED": "1",
                "NIKO_JIRA_DECISION_GATE_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_USE_TOOL, confidence=0.91),
        ) as gate, patch.object(agent, "_start_deep_agent_thread", return_value=True):
            route = agent.handle_message("xem ticket vua nay giup anh", message, delivered.append)

        job = agent.get_active_deep_job("456")
        self.assertEqual(route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertIsNotNone(job)
        self.assertIn("NIKO-101", job.deep_context)
        self.assertIn("Jira issue context", job.deep_context)
        gate.assert_called_once()

    def test_jira_decision_gate_can_ask_for_issue_key(self):
        message = telegram_message_to_gateway(
            {
                "text": "xem ticket nay giup anh",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        agent = self.make_agent()
        delivered = []

        with patch.dict(
            os.environ,
            self.isolated_env({
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_JIRA_TOOLS_ENABLED": "1",
                "NIKO_JIRA_DECISION_GATE_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "Meow",
            }),
            clear=True,
        ), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_ASK_FOR_ISSUE_KEY, confidence=0.91),
        ) as gate, patch.object(agent, "_start_deep_agent_thread", return_value=True) as start_deep:
            route = agent.handle_message("xem ticket nay giup anh", message, delivered.append)

        self.assertEqual(route, ROUTE_JIRA_ISSUE_KEY_REQUIRED)
        self.assertIn("mã issue Jira", delivered[0])
        self.assertTrue(delivered[0].endswith("Meow"))
        self.assertIsNone(agent.get_active_deep_job("456"))
        gate.assert_called_once()
        start_deep.assert_not_called()

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
        with patch.dict(os.environ, self.isolated_env({"CLAUDE_CLI_COMMAND": "fcc-claude -p"}), clear=True):
            self.assertEqual(deep_agent_command(), "fcc-claude -p")

    def test_deep_agent_command_can_override_default(self):
        with patch.dict(
            os.environ,
            self.isolated_env({
                "CLAUDE_CLI_COMMAND": "fcc-claude -p",
                "CLAUDE_DEEP_AGENT_COMMAND": "fcc-claude --model opus[1m] -p",
            }),
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

    def test_load_env_files_reads_only_root_bootstrap_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "NIKO_OPS_HOST=127.0.0.1",
                        "NIKO_RUNTIME_CONFIG_FILE=niko/.runtime/config.json",
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

            with patch.dict(os.environ, self.isolated_env({"PRESERVE_ME": "shell"}), clear=True), patch(
                "niko.config.repo_root", return_value=root
            ):
                load_env_files("telegram")
                self.assertEqual(os.environ["NIKO_OPS_HOST"], "127.0.0.1")
                self.assertEqual(os.environ["NIKO_RUNTIME_CONFIG_FILE"], str(self.runtime_config_file))
                self.assertNotIn("NIKO_REPLY_SUFFIX", os.environ)
                self.assertNotIn("TELEGRAM_BOT_TOKEN", os.environ)
                self.assertEqual(os.environ["PRESERVE_ME"], "shell")

    def test_run_cli_uses_configured_workdir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "claude_sandbox"
            completed = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
            with patch.dict(os.environ, {"CLAUDE_WORKDIR": str(workdir)}, clear=False), patch(
                "niko.runtime.subprocess.run", return_value=completed
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
        with patch.dict(os.environ, self.isolated_env(), clear=True):
            answer = ensure_reply_suffix("Da anh")

        self.assertEqual(answer, "Da anh\n\nMeow")

    def test_sticker_picker_chooses_matching_duck_sticker_by_decided_mood(self):
        thumbs_up = "\U0001f44d"
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"emojis": [thumbs_up]}},
        }
        stickers = [
            {"file_id": "sad-duck", "emoji": "\U0001f610"},
            {"file_id": "happy-duck", "emoji": thumbs_up},
        ]

        sticker = choose_sticker_file_id(stickers, config, "happy", chooser=lambda items: items[0])

        self.assertEqual(sticker, "happy-duck")

    def test_sticker_picker_skips_when_decision_is_no_sticker(self):
        config = {
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"emojis": ["\U0001f44d"]}},
        }

        sticker = choose_sticker_file_id([{"file_id": "duck", "emoji": "\U0001f44d"}], config, "no_sticker")

        self.assertIsNone(sticker)

    def test_sticker_decision_terminal_log_includes_provider_model_and_mood(self):
        decision = StickerMoodDecision(
            mood="happy",
            provider="ollama_nimble",
            model="nimble",
            confidence=0.8123,
            label="happy",
            probabilities={"happy": 0.8123, "no_sticker": 0.1877},
        )

        line = format_sticker_decision_log(decision)

        self.assertIn("provider=ollama_nimble", line)
        self.assertIn("mood=happy", line)
        self.assertIn("model=nimble", line)
        self.assertIn("confidence=0.812", line)

    def test_sticker_startup_log_mentions_ollama_decision_model(self):
        with patch.dict(
            os.environ,
            self.isolated_env({
                "TELEGRAM_STICKERS_ENABLED": "1",
                "TELEGRAM_STICKER_DECISION_MODEL_ENABLED": "1",
                "TELEGRAM_STICKER_DECISION_MODEL_TIMEOUT_SECONDS": "5",
            }),
            clear=True,
        ):
            line = sticker_decision_status_line()

        self.assertIn("Ollama local enabled", line)
        self.assertIn("timeout=5", line)

    def test_maybe_send_sticker_sends_matching_duck_sticker_from_decision_model(self):
        STICKER_SET_CACHE.clear()
        calls = []
        thumbs_up = "\U0001f44d"
        config = {
            "set_name": "UtyaDuck",
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"emojis": [thumbs_up]}},
        }

        def fake_telegram_request(token, method, payload, timeout_seconds=None):
            calls.append((method, payload))
            self.assertEqual(timeout_seconds, 5)
            if method == "getStickerSet":
                return {"stickers": [{"file_id": "happy-duck", "emoji": thumbs_up}]}
            return {}

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "1"}, clear=False), patch(
            "bots.telegram.bot.load_effective_sticker_config", return_value=config
        ), patch(
            "bots.telegram.bot.decide_sticker_mood",
            return_value=StickerMoodDecision(mood="happy", provider="ollama_nimble", model="nimble"),
        ), patch("bots.telegram.bot.telegram_request", side_effect=fake_telegram_request):
            maybe_send_sticker("token", 123, "ok anh", "Da duoc anh")

        self.assertEqual(calls[0], ("getStickerSet", {"name": "UtyaDuck"}))
        self.assertEqual(calls[1], ("sendSticker", {"chat_id": 123, "sticker": "happy-duck"}))

    def test_maybe_send_sticker_skips_when_decision_model_chooses_no_sticker(self):
        STICKER_SET_CACHE.clear()
        config = {
            "set_name": "UtyaDuck",
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"emojis": ["\U0001f44d"]}},
        }

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "1"}, clear=False), patch(
            "bots.telegram.bot.load_effective_sticker_config", return_value=config
        ), patch(
            "bots.telegram.bot.decide_sticker_mood",
            return_value=StickerMoodDecision(mood="no_sticker", provider="ollama_nimble", model="nimble"),
        ), patch("bots.telegram.bot.telegram_request") as telegram_request_mock:
            maybe_send_sticker("token", 123, "nghiem tuc", "Da anh")

        telegram_request_mock.assert_not_called()

    def test_maybe_send_sticker_skips_when_decision_model_fails(self):
        STICKER_SET_CACHE.clear()
        config = {
            "set_name": "UtyaDuck",
            "mode": "smart",
            "mood_priority": ["happy"],
            "moods": {"happy": {"emojis": ["\U0001f44d"]}},
        }

        with patch.dict(os.environ, {"TELEGRAM_STICKERS_ENABLED": "1"}, clear=False), patch(
            "bots.telegram.bot.load_effective_sticker_config", return_value=config
        ), patch(
            "bots.telegram.bot.decide_sticker_mood",
            side_effect=RuntimeError("ollama sticker timeout"),
        ), patch("bots.telegram.bot.telegram_request") as telegram_request_mock:
            maybe_send_sticker("token", 123, "nghiem tuc", "Da anh")

        telegram_request_mock.assert_not_called()

    def test_uncertain_delay_seconds_is_bounded(self):
        with patch.dict(os.environ, {"NIKO_UNCERTAIN_DELAY_SECONDS": "45"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 30.0)

        with patch.dict(os.environ, {"NIKO_UNCERTAIN_DELAY_SECONDS": "2.5"}, clear=False):
            self.assertEqual(uncertain_delay_seconds(), 2.5)


if __name__ == "__main__":
    unittest.main()
