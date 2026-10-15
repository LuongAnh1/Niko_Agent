import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from niko.sdlc.client import (
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_START,
    ROUTE_SDLC_START_JIRA,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcClient,
    detect_sdlc_request,
)


class SdlcClientTests(unittest.TestCase):
    def test_detects_etour_uc1_start_request(self):
        request = detect_sdlc_request("Niko, thuc hien eTour UC1 giup anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_START)
        self.assertEqual(request.dataset, "eTour")
        self.assertEqual(request.use_case, "UC1")

    def test_detects_run_status_request(self):
        request = detect_sdlc_request("run_20261012_120000_abcd1234 den dau roi em?")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_STATUS)
        self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")

    def test_detects_checkpoint_approval_request(self):
        request = detect_sdlc_request("Niko, duyet run_20261012_120000_abcd1234 giup anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_APPROVE)
        self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")

    def test_detects_jira_issue_start_request(self):
        request = detect_sdlc_request("Niko, thực hiện issue SDLC-1 giúp anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_START_JIRA)
        self.assertEqual(request.issue_key, "SDLC-1")

    def test_ignores_unrelated_prompt(self):
        self.assertIsNone(detect_sdlc_request("hom nay bot co on khong em?"))

    def test_start_or_get_run_parses_cli_json(self):
        payload = (
            '{"ok": true, "run_id": "run_20261012_120000_abcd1234", '
            '"source_ref": "eTour UC1", "current_phase": "phase_1", '
            '"status": "checkpoint_pending", "allowed_next_actions": ["approve"]}'
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "SDLC_HARNESS_LLM_MODE": "fake",
                "NIKO_SDLC_SYNC_MODE": "real",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            result = SdlcClient().start_or_get_run(dataset="eTour", use_case="UC1", requested_by="telegram:1")

        self.assertTrue(result.ok)
        self.assertEqual(result.run_id, "run_20261012_120000_abcd1234")
        self.assertEqual(result.source_ref, "eTour UC1")
        self.assertEqual(result.allowed_next_actions, ["approve"])
        command = run.call_args.args[0]
        self.assertIn("start-or-get-run", command)
        self.assertIn("--llm-mode", command)
        self.assertIn("fake", command)
        self.assertEqual(command[-2:], ["--sync-mode", "real"])

    def test_start_from_jira_parses_cli_json(self):
        payload = (
            '{"ok": true, "run_id": "run_20261012_120000_abcd1234", '
            '"source_ref": "jira_issue:SDLC-1", "current_phase": "phase_1", '
            '"status": "checkpoint_pending"}'
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "SDLC_HARNESS_LLM_MODE": "deepseek",
                "NIKO_SDLC_SYNC_MODE": "real",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            result = SdlcClient().start_from_jira(issue_key="SDLC-1", requested_by="telegram:1")

        self.assertTrue(result.ok)
        self.assertEqual(result.source_ref, "jira_issue:SDLC-1")
        command = run.call_args.args[0]
        self.assertIn("start-from-jira", command)
        self.assertIn("SDLC-1", command)
        self.assertIn("--llm-mode", command)
        self.assertIn("deepseek", command)
        self.assertEqual(command[-2:], ["--sync-mode", "real"])

    def test_get_status_parses_cli_json(self):
        payload = (
            '{"ok": true, "run_id": "run_20261012_120000_abcd1234", "status": "done", '
            '"workspace": {"branch": "sdlc/sdlc-1-run_abcd1234", '
            '"pr_url": "https://github.com/example/target/pull/1"}}'
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "done")
        self.assertEqual(result.workspace["branch"], "sdlc/sdlc-1-run_abcd1234")
        self.assertEqual(result.workspace["pr_url"], "https://github.com/example/target/pull/1")
        self.assertIn("get-status", run.call_args.args[0])

    def test_workspace_defaults_to_empty_dict_for_missing_or_invalid_values(self):
        payloads = [
            '{"ok": true, "status": "done"}',
            '{"ok": true, "status": "done", "workspace": "invalid"}',
            '{"ok": true, "status": "done", "workspace": []}',
        ]

        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp_dir, patch.dict(
                os.environ,
                {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
                clear=False,
            ), patch(
                "niko.sdlc.client.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
            ):
                result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

            self.assertEqual(result.workspace, {})

    def test_approve_checkpoint_parses_cli_json(self):
        payload = (
            '{"ok": true, "run_id": "run_20261012_120000_abcd1234", '
            '"status": "waiting_design_approval", "allowed_next_actions": ["approve_checkpoint"], '
            '"last_error": {"error_code": "none", "message": ""}}'
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "SDLC_HARNESS_LLM_MODE": "deepseek",
                "NIKO_SDLC_SYNC_MODE": "real",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            result = SdlcClient().approve_checkpoint(run_id="run_20261012_120000_abcd1234")

        self.assertTrue(result.ok)
        self.assertEqual(result.action, ROUTE_SDLC_APPROVE)
        self.assertEqual(result.status, "waiting_design_approval")
        self.assertEqual(result.last_error["error_code"], "none")
        command = run.call_args.args[0]
        self.assertIn("approve-checkpoint", command)
        self.assertIn("--run-id", command)
        self.assertIn("run_20261012_120000_abcd1234", command)
        self.assertIn("--llm-mode", command)
        self.assertIn("deepseek", command)
        self.assertEqual(command[-2:], ["--sync-mode", "real"])

    def test_continue_and_sync_run_pass_explicit_sync_mode(self):
        payload = '{"ok": true, "run_id": "run_20261012_120000_abcd1234", "status": "code_review_ready"}'

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "SDLC_HARNESS_LLM_MODE": "fake",
                "NIKO_SDLC_SYNC_MODE": "both",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            continued = SdlcClient().continue_run(run_id="run_20261012_120000_abcd1234")
            synced = SdlcClient().sync_run(run_id="run_20261012_120000_abcd1234")

        self.assertEqual(continued.action, ROUTE_SDLC_CONTINUE)
        self.assertEqual(synced.action, ROUTE_SDLC_SYNC)
        continue_command = run.call_args_list[0].args[0]
        sync_command = run.call_args_list[1].args[0]
        self.assertEqual(continue_command[-2:], ["--sync-mode", "both"])
        self.assertEqual(sync_command[-2:], ["--sync-mode", "both"])

    def test_invalid_sync_mode_falls_back_to_mock(self):
        payload = '{"ok": true, "run_id": "run_20261012_120000_abcd1234"}'
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "NIKO_SDLC_SYNC_MODE": "unsafe",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ) as run:
            SdlcClient().sync_run(run_id="run_20261012_120000_abcd1234")

        self.assertEqual(run.call_args.args[0][-2:], ["--sync-mode", "mock"])

    def test_returns_config_error_when_workdir_is_missing(self):
        with patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": "missing-sdlc-harness-dir-for-test"},
            clear=False,
        ):
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_config_error")

    def test_returns_invalid_json_error(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
        ):
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_invalid_json")

    def test_returns_cli_failed_error(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=2, stdout="", stderr="missing run"),
        ):
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_cli_failed")
        self.assertIn("exit code 2", result.message)
        self.assertNotIn("missing run", result.message)

    def test_keeps_business_error_json_from_nonzero_exit(self):
        run_id = "run_20261012_120000_abcd1234"
        error_codes = [
            "checkpoint_not_current",
            "github_pr_identity_mismatch",
            "github_pr_not_open",
            "github_pr_ready_failed",
            "run_workspace_locked",
        ]

        for error_code in error_codes:
            with self.subTest(error_code=error_code):
                payload = json.dumps(
                    {
                        "ok": False,
                        "run_id": run_id,
                        "error_code": error_code,
                        "message": f"Business error: {error_code}",
                    }
                )
                with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
                    os.environ,
                    {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
                    clear=False,
                ), patch(
                    "niko.sdlc.client.subprocess.run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout=payload,
                        stderr="raw stderr must stay private",
                    ),
                ):
                    result = SdlcClient().approve_checkpoint(run_id=run_id)

                self.assertFalse(result.ok)
                self.assertEqual(result.run_id, run_id)
                self.assertEqual(result.error_code, error_code)
                self.assertEqual(result.message, f"Business error: {error_code}")
                self.assertNotIn("raw stderr", result.message)

    def test_nonzero_exit_with_non_error_json_falls_back_safely(self):
        payload = '{"ok": true, "message": "unexpected success payload"}'

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout=payload, stderr="private diagnostic"),
        ), patch("niko.sdlc.client.default_runtime_logger") as runtime_logger:
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_cli_failed")
        self.assertNotIn("private diagnostic", result.message)
        logged_error_data = runtime_logger.return_value.event.call_args_list[-1].kwargs["data"]
        self.assertNotIn("private diagnostic", str(logged_error_data))
        self.assertNotIn("unexpected success payload", str(logged_error_data))

    def test_normalizes_run_not_found_payload(self):
        payload = (
            '{"ok": false, "run_id": "run_20261012_120000_abcd1234", '
            '"error_code": "run_not_found", "message": "Run not found"}'
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout=payload, stderr=""),
        ):
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "run_not_found")
        self.assertEqual(result.message, "Run not found")

    def test_returns_timeout_error(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "SDLC_HARNESS_WORKDIR": temp_dir,
                "SDLC_HARNESS_PYTHON_COMMAND": "python",
                "SDLC_HARNESS_TIMEOUT_SECONDS": "1",
            },
            clear=False,
        ), patch(
            "niko.sdlc.client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=1),
        ):
            result = SdlcClient().get_status(run_id="run_20261012_120000_abcd1234")

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "sdlc_timeout")


if __name__ == "__main__":
    unittest.main()
