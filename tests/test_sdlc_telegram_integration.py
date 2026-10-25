"""Telegram-to-SDLC integration coverage across the HTTP API boundary."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
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


def _initial_states() -> dict[str, dict]:
    return {
        CONTINUE_RUN_ID: {
            "ok": True,
            "run_id": CONTINUE_RUN_ID,
            "source_ref": "fixture:eTour:UC1",
            "current_phase": "implementation",
            "status": "implementation_ready",
            "summary_for_user": "Implementation plan is ready.",
            "allowed_next_actions": ["continue_run", "get_status"],
        },
        SYNC_RUN_ID: {
            "ok": True,
            "run_id": SYNC_RUN_ID,
            "current_phase": "code_review",
            "status": "code_review_approval_sync_failed",
            "summary_for_user": "PR is ready; tool sync is incomplete.",
            "allowed_next_actions": ["sync_run", "get_status"],
            "last_error": {"error_code": "jira_sync_failed", "message": "fixture failure"},
            "workspace": {"pr_url": PR_URL, "lifecycle": "ready_for_review", "pr_number": 27},
        },
        APPROVAL_RUN_ID: {
            "ok": True,
            "run_id": APPROVAL_RUN_ID,
            "current_phase": "code_review",
            "status": "code_review_ready",
            "summary_for_user": "Draft PR is waiting for approval.",
            "allowed_next_actions": ["approve_checkpoint", "get_status", "sync_run"],
            "pending_checkpoint": {"type": "code_review", "pull_request_url": PR_URL},
        },
    }


def _action_result(run_id: str, action: str) -> dict:
    if run_id == CONTINUE_RUN_ID and action == "continue_run":
        return {
            "ok": True,
            "run_id": run_id,
            "current_phase": "code_review",
            "status": "code_review_ready",
            "summary_for_user": f"Draft PR is ready: {PR_URL}",
            "allowed_next_actions": ["approve_checkpoint", "get_status", "sync_run"],
            "pending_checkpoint": {"type": "code_review", "pull_request_url": PR_URL},
            "workspace": {
                "branch": "feature/sdlc-smoke-a11ce001",
                "head_sha": "0123456789abcdef0123456789abcdef01234567",
                "lifecycle": "draft_pr",
                "pr_number": 27,
                "pr_url": PR_URL,
            },
        }
    if run_id == SYNC_RUN_ID and action == "sync_run":
        return {
            "ok": True,
            "run_id": run_id,
            "current_phase": "code_review",
            "status": "code_review_approved",
            "summary_for_user": "Approval sync completed.",
            "allowed_next_actions": ["get_status"],
            "workspace": {"pr_url": PR_URL, "lifecycle": "ready_for_review", "pr_number": 27},
            "last_sync": {"status": "mock_synced", "targets": []},
        }
    return {
        "ok": False,
        "run_id": run_id,
        "error_code": "fixture_action_not_supported",
        "message": f"Unsupported fixture action: {action}",
    }


class _SdlcApiHandler(BaseHTTPRequestHandler):
    server: "_SdlcApiServer"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path, None))
        if self.path.startswith("/api/v1/runs/"):
            run_id = self.path.rsplit("/", 1)[-1]
            payload = self.server.states.get(run_id) or {
                "ok": False,
                "run_id": run_id,
                "error_code": "run_not_found",
                "message": "Run not found.",
            }
            self._send(200 if payload.get("ok") else 404, payload)
            return
        if self.path.startswith("/api/v1/operations/"):
            operation_id = self.path.rsplit("/", 1)[-1]
            operation = self.server.operations[operation_id]
            if operation["action"] == "continue_run" and not self.server.continue_release.is_set():
                self._send(
                    200,
                    {
                        "ok": True,
                        "operation_id": operation_id,
                        "operation_status": "running",
                        "run_id": operation["run_id"],
                    },
                )
                return
            result = operation["result"]
            if result.get("ok"):
                self.server.states[operation["run_id"]] = result
                payload = {
                    "ok": True,
                    "operation_id": operation_id,
                    "operation_status": "succeeded",
                    "run_id": operation["run_id"],
                    "result": result,
                    "error": None,
                }
            else:
                payload = {
                    "ok": True,
                    "operation_id": operation_id,
                    "operation_status": "failed",
                    "run_id": operation["run_id"],
                    "result": None,
                    "error": result,
                }
            self._send(200, payload)
            return
        self._send(404, {"ok": False, "error_code": "route_not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append(("POST", self.path, body))
        run_id = self.path.split("/")[4]
        action = str(body.get("action") or "")
        operation_id = f"op_fixture_{len(self.server.operations) + 1}"
        self.server.operations[operation_id] = {
            "run_id": run_id,
            "action": action,
            "result": _action_result(run_id, action),
        }
        if action == "continue_run":
            self.server.continue_started.set()
        self._send(
            202,
            {
                "ok": True,
                "operation_id": operation_id,
                "operation_status": "queued",
                "run_id": run_id,
            },
        )

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class _SdlcApiServer(ThreadingHTTPServer):
    requests: list[tuple[str, str, dict | None]]
    states: dict[str, dict]
    operations: dict[str, dict]
    continue_started: threading.Event
    continue_release: threading.Event


class SdlcTelegramIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.niko_state_path = self.temp_path / "niko-state"
        self.runtime_config_path = self.temp_path / "runtime-config.json"
        self.server = _SdlcApiServer(("127.0.0.1", 0), _SdlcApiHandler)
        self.server.requests = []
        self.server.states = _initial_states()
        self.server.operations = {}
        self.server.continue_started = threading.Event()
        self.server.continue_release = threading.Event()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        host, port = self.server.server_address

        self._env_patch = patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": "0",
                "NIKO_SDLC_SYNC_MODE": "mock",
                "NIKO_SDLC_API_BASE_URL": f"http://{host}:{port}",
                "NIKO_SDLC_API_REQUEST_TIMEOUT_SECONDS": "1",
                "NIKO_SDLC_API_OPERATION_TIMEOUT_SECONDS": "5",
                "NIKO_SDLC_API_POLL_INTERVAL_SECONDS": "0.01",
                "NIKO_RUNTIME_LOG_ENABLED": "1",
                "NIKO_MEMORY_WRITE_ENABLED": "1",
                "NIKO_REPLY_SUFFIX": "",
                "NIKO_STATE_DIR": str(self.niko_state_path),
                "NIKO_RUNTIME_CONFIG_FILE": str(self.runtime_config_path),
                "TELEGRAM_STICKERS_ENABLED": "0",
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
        self.server.continue_release.set()
        wait_until(lambda: self.app.sdlc_job_runner.get_active(CONTINUE_RUN_ID) is None)
        wait_until(lambda: self.app.sdlc_job_runner.get_active(SYNC_RUN_ID) is None)
        self._send_patch.stop()
        self._gateway_patch.stop()
        self._env_patch.stop()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self._temp_dir.cleanup()

    def test_continue_status_duplicate_and_final_reply_cross_real_http_boundary(self) -> None:
        self._handle(f"continue {CONTINUE_RUN_ID}")
        self.assertTrue(wait_until(lambda: len(self._messages()) >= 1))
        self.assertTrue(wait_until(self.server.continue_started.is_set))

        self._handle(f"status {CONTINUE_RUN_ID}")
        self._handle(f"continue {CONTINUE_RUN_ID}")
        self.assertTrue(any("implementation" in item.lower() for item in self._messages()))
        self.assertEqual(self._action_count("continue_run"), 1)

        self.server.continue_release.set()
        self.assertTrue(wait_until(lambda: any("code_review_ready" in item for item in self._messages())))
        final_reply = next(item for item in self._messages() if "code_review_ready" in item)
        self.assertEqual(final_reply.count(PR_URL), 1)
        self.assertTrue(wait_until(lambda: self.app.sdlc_job_runner.get_active(CONTINUE_RUN_ID) is None))

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

    def test_sync_retry_and_disabled_approval_never_submit_approval_action(self) -> None:
        self._handle(f"approve {APPROVAL_RUN_ID}")
        self.assertTrue(wait_until(lambda: any("Code Review" in item for item in self._messages())))

        self._handle(f"resync {SYNC_RUN_ID}")
        self.assertTrue(wait_until(lambda: any("code_review_approved" in item for item in self._messages())))
        self.assertTrue(wait_until(lambda: self.app.sdlc_job_runner.get_active(SYNC_RUN_ID) is None))
        self.assertEqual(self._action_count("sync_run"), 1)
        self.assertEqual(self._action_count("approve_checkpoint"), 0)

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

    def _action_count(self, action: str) -> int:
        return sum(
            method == "POST" and isinstance(body, dict) and body.get("action") == action
            for method, _, body in self.server.requests
        )


if __name__ == "__main__":
    unittest.main()
