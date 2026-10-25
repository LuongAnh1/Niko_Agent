from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from threading import Thread
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from niko.ops.config_schema import MASKED_SECRET_VALUE, config_field_index, config_snapshot
from niko.sdlc.client import (
    ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT,
    ROUTE_SDLC_START_JIRA,
    HttpSdlcTransport,
    SdlcClient,
)
from niko.sdlc.formatting import format_sdlc_reply


class _ApiFixtureHandler(BaseHTTPRequestHandler):
    server: "_ApiFixtureServer"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path.startswith("/api/v1/runs/"):
            self._send(
                200,
                {
                    "ok": True,
                    "run_id": self.path.rsplit("/", 1)[-1],
                    "source_ref": "jira_issue:SDLC-7",
                    "current_phase": "requirement_drafting",
                    "status": "waiting_requirement_approval",
                    "summary_for_user": "Requirement ready.",
                    "allowed_next_actions": ["approve_checkpoint", "get_status"],
                },
            )
            return
        if self.path.startswith("/api/v1/operations/"):
            operation_id = self.path.rsplit("/", 1)[-1]
            self.server.poll_count += 1
            if self.server.operation_mode == "pending":
                self._send(
                    200,
                    {
                        "ok": True,
                        "operation_id": operation_id,
                        "operation_status": "running",
                        "run_id": self.server.run_id,
                        "result": None,
                        "error": None,
                    },
                )
                return
            if self.server.operation_mode == "failed":
                self._send(
                    200,
                    {
                        "ok": True,
                        "operation_id": operation_id,
                        "operation_status": "failed",
                        "run_id": self.server.run_id,
                        "result": None,
                        "error": {
                            "ok": False,
                            "error_code": "invalid_run_action",
                            "message": "Action is not allowed.",
                            "run_id": self.server.run_id,
                        },
                    },
                )
                return
            if self.server.operation_mode == "interrupted":
                self._send(
                    200,
                    {
                        "ok": True,
                        "operation_id": operation_id,
                        "operation_status": "interrupted",
                        "run_id": self.server.run_id,
                        "result": None,
                        "error": {
                            "ok": False,
                            "error_code": "operation_interrupted",
                            "message": "Operation was interrupted by API restart.",
                            "run_id": self.server.run_id,
                        },
                    },
                )
                return
            status = "running" if self.server.poll_count == 1 else "succeeded"
            self._send(
                200,
                {
                    "ok": True,
                    "operation_id": operation_id,
                    "operation_status": status,
                    "run_id": self.server.run_id,
                    "result": None
                    if status == "running"
                    else {
                        "ok": True,
                        "run_id": self.server.run_id,
                        "source_ref": "jira_issue:SDLC-7",
                        "current_phase": "requirement_drafting",
                        "status": "waiting_requirement_approval",
                        "summary_for_user": "Requirement ready.",
                        "allowed_next_actions": ["approve_checkpoint", "get_status"],
                    },
                    "error": None,
                },
            )
            return
        self._send(404, {"ok": False, "error_code": "route_not_found", "message": "Not found."})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append(("POST", self.path, dict(self.headers), body))
        self.server.poll_count = 0
        self._send(
            202,
            {
                "ok": True,
                "operation_id": "op_fixture_1",
                "operation_status": "queued",
                "run_id": self.server.run_id if "/actions" in self.path else None,
                "status_url": "/api/v1/operations/op_fixture_1",
                "idempotency_replayed": False,
            },
        )

    def _send(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _ApiFixtureServer(ThreadingHTTPServer):
    requests: list[tuple[str, str, dict[str, str], dict[str, object] | None]]
    poll_count: int
    operation_mode: str
    run_id: str


class SdlcHttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _ApiFixtureServer(("127.0.0.1", 0), _ApiFixtureHandler)
        self.server.requests = []
        self.server.poll_count = 0
        self.server.operation_mode = "success"
        self.server.run_id = "run_20261018_120000_abcd1234"
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.environment = patch.dict(
            os.environ,
            {
                "NIKO_SDLC_API_BASE_URL": f"http://{host}:{port}",
                "NIKO_SDLC_API_TOKEN": "fixture-token",
                "NIKO_SDLC_API_REQUEST_TIMEOUT_SECONDS": "1",
                "NIKO_SDLC_API_OPERATION_TIMEOUT_SECONDS": "1",
                "NIKO_SDLC_API_POLL_INTERVAL_SECONDS": "0.01",
                "NIKO_SDLC_SYNC_MODE": "mock",
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_read_status_uses_bearer_auth_and_preserves_result_contract(self) -> None:
        result = SdlcClient().get_status(run_id=self.server.run_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.run_id, self.server.run_id)
        method, path, headers, body = self.server.requests[0]
        self.assertEqual((method, path, body), ("GET", f"/api/v1/runs/{self.server.run_id}", None))
        self.assertEqual(headers["Authorization"], "Bearer fixture-token")

    def test_create_jira_run_submits_then_polls_to_terminal_result(self) -> None:
        result = SdlcClient().start_from_jira(issue_key="SDLC-7", requested_by="telegram:7")

        self.assertTrue(result.ok)
        self.assertEqual(result.action, ROUTE_SDLC_START_JIRA)
        self.assertEqual(result.operation_id, "op_fixture_1")
        method, path, headers, body = self.server.requests[0]
        self.assertEqual((method, path), ("POST", "/api/v1/runs"))
        self.assertEqual(
            body,
            {
                "source": {"type": "jira", "issue_key": "SDLC-7"},
                "requested_by": "telegram:7",
                "sync_mode": "mock",
            },
        )
        self.assertTrue(headers["Idempotency-Key"].startswith("niko-"))
        self.assertGreaterEqual(self.server.poll_count, 2)

    def test_action_mapping_includes_actor_and_action_arguments(self) -> None:
        result = SdlcClient().answer_clarification(
            run_id=self.server.run_id,
            question_id="Q-1",
            answer="Use the normalized site identifier.",
            answered_by="telegram:9",
        )

        self.assertTrue(result.ok)
        _, path, _, body = self.server.requests[0]
        self.assertEqual(path, f"/api/v1/runs/{self.server.run_id}/actions")
        self.assertEqual(body["action"], "answer_clarification")
        self.assertEqual(body["requested_by"], "telegram:9")
        self.assertEqual(
            body["arguments"],
            {
                "question_id": "Q-1",
                "answer": "Use the normalized site identifier.",
                "answered_by": "telegram:9",
            },
        )

    def test_policy_default_challenge_uses_outer_actor_and_minimal_arguments(self) -> None:
        result = SdlcClient().challenge_policy_default(
            run_id=self.server.run_id,
            assumption_id="ASM-1",
            instruction="Ask the owner for a measurable target.",
            requested_by="telegram:9",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.action, ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT)
        _, path, _, body = self.server.requests[0]
        self.assertEqual(path, f"/api/v1/runs/{self.server.run_id}/actions")
        self.assertEqual(body["action"], "challenge_policy_default")
        self.assertEqual(body["requested_by"], "telegram:9")
        self.assertEqual(
            body["arguments"],
            {
                "assumption_id": "ASM-1",
                "instruction": "Ask the owner for a measurable target.",
            },
        )

    def test_all_supported_control_methods_map_to_api_actions(self) -> None:
        cases = (
            ("approve_checkpoint", "approve_checkpoint"),
            ("continue_run", "continue_run"),
            ("revalidate_implementation", "revalidate_implementation"),
            ("revalidate_implementation_plan", "revalidate_implementation_plan"),
            ("revalidate_design", "revalidate_design"),
            ("revalidate_requirement", "revalidate_requirement"),
            ("clarify_more", "clarify_more"),
            ("sync_run", "sync_run"),
        )

        for method_name, expected_action in cases:
            with self.subTest(method=method_name):
                self.server.requests.clear()
                self.server.poll_count = 0
                result = getattr(SdlcClient(), method_name)(
                    run_id=self.server.run_id,
                    requested_by="telegram:11",
                )

                self.assertTrue(result.ok)
                method, path, _, body = self.server.requests[0]
                self.assertEqual(method, "POST")
                self.assertEqual(path, f"/api/v1/runs/{self.server.run_id}/actions")
                self.assertEqual(body["action"], expected_action)
                self.assertEqual(body["requested_by"], "telegram:11")
                self.assertEqual(body["arguments"], {})

    def test_terminal_operation_error_is_preserved(self) -> None:
        self.server.operation_mode = "failed"

        result = SdlcClient().continue_run(run_id=self.server.run_id, requested_by="telegram:7")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "invalid_run_action")
        self.assertEqual(result.operation_id, "op_fixture_1")
        self.assertEqual(result.run_id, self.server.run_id)

    def test_interrupted_operation_is_preserved_without_new_submit(self) -> None:
        self.server.operation_mode = "interrupted"

        result = SdlcClient().continue_run(run_id=self.server.run_id, requested_by="telegram:7")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "operation_interrupted")
        self.assertEqual(result.operation_id, "op_fixture_1")
        self.assertEqual(result.run_id, self.server.run_id)

    def test_http_error_json_is_preserved_for_supported_statuses(self) -> None:
        transport = HttpSdlcTransport()
        for status in (400, 401, 404, 409, 503):
            with self.subTest(status=status):
                payload = {
                    "ok": False,
                    "error_code": f"fixture_{status}",
                    "message": "Fixture error.",
                }
                error = HTTPError(
                    url="http://fixture/api/v1/runs/x",
                    code=status,
                    msg="error",
                    hdrs=None,
                    fp=BytesIO(json.dumps(payload).encode("utf-8")),
                )
                with patch("niko.sdlc.client.urlopen", side_effect=error):
                    result = transport.get_status(run_id="run_fixture")
                self.assertEqual(result, payload)

    def test_invalid_json_and_unavailable_api_return_stable_errors(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"not-json"
        with patch("niko.sdlc.client.urlopen", return_value=response):
            invalid = HttpSdlcTransport().get_status(run_id="run_fixture")
        with patch("niko.sdlc.client.urlopen", side_effect=URLError("offline")):
            unavailable = HttpSdlcTransport().get_status(run_id="run_fixture")

        self.assertEqual(invalid["error_code"], "sdlc_invalid_json")
        self.assertEqual(unavailable["error_code"], "sdlc_api_unavailable")

    def test_poll_timeout_reports_pending_without_new_operation(self) -> None:
        self.server.operation_mode = "pending"
        os.environ["NIKO_SDLC_API_OPERATION_TIMEOUT_SECONDS"] = "0.1"

        result = SdlcClient().sync_run(run_id=self.server.run_id, requested_by="telegram:7")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_operation_pending")
        self.assertEqual(result.operation_id, "op_fixture_1")
        reply = format_sdlc_reply(result)
        self.assertIn("op_fixture_1", reply)
        self.assertIn("không tự tạo operation mới", reply)

    def test_config_snapshot_exposes_only_api_transport_settings(self) -> None:
        snapshot = config_snapshot()
        business = next(section for section in snapshot["sections"] if section["id"] == "business")
        fields = {field["name"]: field for field in business["fields"]}

        self.assertEqual(fields["NIKO_SDLC_API_TOKEN"]["value"], MASKED_SECRET_VALUE)
        self.assertNotIn("NIKO_SDLC_TRANSPORT", fields)
        for removed in (
            "SDLC_HARNESS_WORKDIR",
            "SDLC_HARNESS_PYTHON_COMMAND",
            "SDLC_HARNESS_TIMEOUT_SECONDS",
            "SDLC_HARNESS_LLM_MODE",
        ):
            self.assertNotIn(removed, fields)
            self.assertNotIn(removed, config_field_index())


class SdlcHttpRetryTests(unittest.TestCase):
    def test_submit_retry_reuses_idempotency_key(self) -> None:
        transport = HttpSdlcTransport()
        submit_keys: list[str] = []

        def fake_request(method, path, *, body=None, idempotency_key=""):
            if method == "POST":
                submit_keys.append(idempotency_key)
                if len(submit_keys) == 1:
                    return {
                        "ok": False,
                        "error_code": "sdlc_api_request_timeout",
                        "message": "timeout",
                    }
                return {
                    "ok": True,
                    "operation_id": "op_retry_1",
                    "operation_status": "queued",
                    "run_id": "run_retry_1",
                }
            return {
                "ok": True,
                "operation_id": "op_retry_1",
                "operation_status": "succeeded",
                "run_id": "run_retry_1",
                "result": {"ok": True, "run_id": "run_retry_1", "status": "done"},
                "error": None,
            }

        with patch.object(transport, "_request_json", side_effect=fake_request), patch.dict(
            os.environ,
            {"NIKO_RUNTIME_LOG_ENABLED": "0"},
            clear=False,
        ):
            payload = transport.create_run(
                source={"type": "dataset", "dataset": "eTour", "use_case": "UC1"},
                requested_by="telegram:1",
                event="test",
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(len(submit_keys), 2)
        self.assertEqual(submit_keys[0], submit_keys[1])

    def test_deprecated_cli_environment_does_not_change_api_client(self) -> None:
        transport = MagicMock()
        transport.get_status.return_value = {
            "ok": True,
            "run_id": "run_api",
            "status": "done",
        }
        client = SdlcClient(transport=transport)

        with patch.dict(
            os.environ,
            {
                "NIKO_SDLC_TRANSPORT": "cli",
                "SDLC_HARNESS_WORKDIR": "missing-workdir",
                "SDLC_HARNESS_PYTHON_COMMAND": "missing-python",
                "SDLC_HARNESS_TIMEOUT_SECONDS": "1",
                "SDLC_HARNESS_LLM_MODE": "fake",
            },
            clear=False,
        ):
            result = client.get_status(run_id="run_api")

        self.assertTrue(result.ok)
        transport.get_status.assert_called_once_with(run_id="run_api")


if __name__ == "__main__":
    unittest.main()
