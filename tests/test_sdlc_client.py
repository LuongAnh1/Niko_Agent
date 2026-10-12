import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from niko.sdlc.client import ROUTE_SDLC_START, ROUTE_SDLC_STATUS, SdlcClient, detect_sdlc_request


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
            {"SDLC_HARNESS_WORKDIR": temp_dir, "SDLC_HARNESS_PYTHON_COMMAND": "python"},
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
        self.assertIn("start-or-get-run", run.call_args.args[0])

    def test_get_status_parses_cli_json(self):
        payload = '{"ok": true, "run_id": "run_20261012_120000_abcd1234", "status": "done"}'

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
        self.assertIn("get-status", run.call_args.args[0])

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
        self.assertIn("missing run", result.message)

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
