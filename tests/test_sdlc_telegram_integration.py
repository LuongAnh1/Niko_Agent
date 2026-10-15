from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bots.telegram import bot as telegram_bot
from niko.app import NikoApp
from niko.gateway import GatewayRunner
from niko.harness.runtime_log import default_runtime_logger
from niko.harness.trace import TraceLogger
from niko.memory.store import MemoryStore


CONTINUE_RUN_ID = "run_20261015_120000_a11ce001"
SYNC_RUN_ID = "run_20261015_120001_a11ce002"
APPROVAL_RUN_ID = "run_20261015_120002_a11ce003"
PR_URL = "https://github.test/example/target/pull/27"


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class SdlcTelegramIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.state_path = self.temp_path / "sdlc-state.json"
        self.command_log_path = self.temp_path / "commands.jsonl"
        self.continue_started_path = self.temp_path / "continue.started"
        self.continue_release_path = self.temp_path / "continue.release"
        self.niko_state_path = self.temp_path / "niko-state"
        self.runtime_config_path = self.temp_path / "runtime-config.json"
        self.fixture_workdir = Path(__file__).resolve().parent / "fixtures" / "sdlc_cli_stub"
        self._write_initial_state()

        self._env_patch = patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": "0",
                "NIKO_SDLC_SYNC_MODE": "mock",
                "NIKO_RUNTIME_LOG_ENABLED": "1",
                "NIKO_MEMORY_WRITE_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "",
                "NIKO_STATE_DIR": str(self.niko_state_path),
                "NIKO_RUNTIME_CONFIG_FILE": str(self.runtime_config_path),
                "TELEGRAM_STICKERS_ENABLED": "0",
                "SDLC_HARNESS_WORKDIR": str(self.fixture_workdir),
                "SDLC_HARNESS_PYTHON_COMMAND": sys.executable,
                "SDLC_HARNESS_LLM_MODE": "fake",
                "SDLC_HARNESS_TIMEOUT_SECONDS": "10",
                "SDLC_SMOKE_STATE_FILE": str(self.state_path),
                "SDLC_SMOKE_COMMAND_LOG": str(self.command_log_path),
                "SDLC_SMOKE_CONTINUE_STARTED": str(self.continue_started_path),
                "SDLC_SMOKE_CONTINUE_RELEASE": str(self.continue_release_path),
                "SDLC_SMOKE_CONTINUE_WAIT_SECONDS": "5",
            },
            clear=False,
        )
        self._env_patch.start()

        self.memory_store = MemoryStore(self.temp_path / "memory.sqlite3")
        self.trace_logger = TraceLogger(self.temp_path / "traces", enabled=True)
        self.app = NikoApp(memory_store=self.memory_store, trace_logger=self.trace_logger)
        self.gateway_runner = GatewayRunner(app=self.app)
        self.sent_messages: list[str] = []
        self._messages_lock = threading.Lock()
        self._gateway_patch = patch("bots.telegram.bot.GATEWAY_RUNNER", self.gateway_runner)
        self._send_patch = patch("bots.telegram.bot.send_message", side_effect=self._record_message)
        self._gateway_patch.start()
        self._send_patch.start()

    def tearDown(self) -> None:
        self.continue_release_path.touch(exist_ok=True)
        wait_until(lambda: self.app.sdlc_job_runner.get_active(CONTINUE_RUN_ID) is None)
        wait_until(lambda: self.app.sdlc_job_runner.get_active(SYNC_RUN_ID) is None)
        self._send_patch.stop()
        self._gateway_patch.stop()
        self._env_patch.stop()
        self._temp_dir.cleanup()

    def test_continue_status_duplicate_and_final_reply_cross_real_subprocess_boundary(self) -> None:
        self._handle(f"chạy tiếp {CONTINUE_RUN_ID}")

        self.assertTrue(wait_until(lambda: len(self._messages()) >= 1))
        self.assertIn("xử lý nền", self._messages()[0])
        self.assertTrue(wait_until(self.continue_started_path.exists))

        self._handle(f"trạng thái {CONTINUE_RUN_ID}")
        self._handle(f"continue {CONTINUE_RUN_ID}")

        self.assertTrue(wait_until(lambda: any("không tạo tiến trình thứ hai" in item for item in self._messages())))
        self.assertTrue(any("implementation" in item.lower() for item in self._messages()))
        self.assertEqual(self._command_names().count("continue-run"), 1)

        self.continue_release_path.touch()
        self.assertTrue(wait_until(lambda: any("code_review_ready" in item for item in self._messages())))
        final_reply = next(item for item in self._messages() if "code_review_ready" in item)
        self.assertEqual(final_reply.count(PR_URL), 1)
        self.assertTrue(wait_until(lambda: self.app.sdlc_job_runner.get_active(CONTINUE_RUN_ID) is None))

        commands = self._commands()
        self.assertEqual(sum(item["command"] == "continue-run" for item in commands), 1)
        self.assertGreaterEqual(sum(item["command"] == "get-status" for item in commands), 3)
        continue_command = next(item for item in commands if item["command"] == "continue-run")
        self.assertIn("fake", continue_command["argv"])
        self.assertIn("mock", continue_command["argv"])

        events = self.trace_logger.read_events(limit=100)
        self.assertEqual(sum(event.get("type") == "turn_end" for event in events), 3)
        runtime_events = default_runtime_logger().read_events(limit=200, source="sdlc")
        event_names = {event.get("event") for event in runtime_events}
        self.assertTrue(
            {
                "sdlc_job_queued",
                "sdlc_job_started",
                "sdlc_job_duplicate",
                "sdlc_job_finished",
                "sdlc_job_result_delivered",
            }.issubset(event_names)
        )

    def test_sync_retry_and_disabled_approval_never_dispatch_approval_subprocess(self) -> None:
        self._handle(f"duyệt {APPROVAL_RUN_ID}")
        self.assertTrue(wait_until(lambda: any("chưa duyệt Code Review" in item for item in self._messages())))

        self._handle(f"đồng bộ lại {SYNC_RUN_ID}")
        self.assertTrue(wait_until(lambda: any("code_review_approved" in item for item in self._messages())))
        self.assertTrue(wait_until(lambda: self.app.sdlc_job_runner.get_active(SYNC_RUN_ID) is None))

        commands = self._commands()
        self.assertEqual(sum(item["command"] == "sync-run" for item in commands), 1)
        self.assertFalse(any(item["command"] == "approve-checkpoint" for item in commands))
        sync_command = next(item for item in commands if item["command"] == "sync-run")
        self.assertIn("mock", sync_command["argv"])

    def _handle(self, text: str) -> None:
        telegram_bot.handle_message(
            "test-token",
            {
                "message_id": int(time.time_ns() % 1_000_000),
                "text": text,
                "from": {"id": 123, "username": "integration_user", "first_name": "Integration"},
                "chat": {"id": 456, "type": "private"},
            },
            set(),
            set(),
            {},
            "NikoBot",
        )

    def _record_message(self, _token: str, _chat_id: int, text: str, parse_mode=None) -> None:
        with self._messages_lock:
            self.sent_messages.append(text)

    def _messages(self) -> list[str]:
        with self._messages_lock:
            return list(self.sent_messages)

    def _commands(self) -> list[dict]:
        if not self.command_log_path.exists():
            return []
        return [json.loads(line) for line in self.command_log_path.read_text(encoding="utf-8").splitlines() if line]

    def _command_names(self) -> list[str]:
        return [item["command"] for item in self._commands()]

    def _write_initial_state(self) -> None:
        template_path = self.fixture_workdir / "smoke_state.template.json"
        self.state_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
