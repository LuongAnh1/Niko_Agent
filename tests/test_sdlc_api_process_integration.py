"""Cutover smoke through Niko and the real SDLC Harness HTTP process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen

from niko.app import NikoApp
from niko.sdlc.client import ROUTE_SDLC_APPROVE, ROUTE_SDLC_START, ROUTE_SDLC_STATUS


NIKO_ROOT = Path(__file__).resolve().parents[1]
SDLC_ROOT = Path(
    os.environ.get("NIKO_SDLC_INTEGRATION_ROOT", str(NIKO_ROOT.parent / "SDLC_Harness"))
).expanduser().resolve()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _create_typescript_target(root: Path) -> tuple[Path, Path]:
    """Create the smallest local target accepted by the harness profile contract."""

    target = root / "target-repo"
    remote = root / "target.git"
    editable_roots = {
        "web": ["apps/web/src"],
        "api": ["apps/api/src"],
        "contracts": ["packages/contracts/src"],
        "database": ["packages/db/src", "packages/db/prisma"],
        "e2e": ["tests/e2e"],
    }
    protected_paths = [
        ".sdlc",
        ".github",
        "package.json",
        "package-lock.json",
        "compose.yaml",
        "tsconfig.json",
        "tsconfig.base.json",
        "vitest.config.ts",
        "playwright.config.ts",
        "apps/web/package.json",
        "apps/web/tsconfig.json",
        "apps/web/vite.config.ts",
        "apps/api/package.json",
        "apps/api/tsconfig.json",
        "packages/contracts/package.json",
        "packages/contracts/tsconfig.json",
        "packages/db/package.json",
        "packages/db/prisma7.config.ts",
        "packages/db/tsconfig.json",
    ]
    verification_stage_ids = [
        "dependency_setup",
        "database_ready",
        "migrate_and_seed",
        "typecheck",
        "unit",
        "api_database_integration",
        "frontend_build",
        "service_health",
        "browser_e2e",
        "cleanup",
    ]
    for paths in editable_roots.values():
        for relative in paths:
            path = target / relative
            path.mkdir(parents=True, exist_ok=True)
            (path / ".gitkeep").write_text("", encoding="utf-8")
    (target / ".github").mkdir(parents=True)
    (target / ".github/.gitkeep").write_text("", encoding="utf-8")
    (target / "package.json").write_text(
        json.dumps({"name": "niko-sdlc-integration-target", "version": "1.0.0", "private": True}) + "\n",
        encoding="utf-8",
    )
    (target / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "niko-sdlc-integration-target",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "requires": True,
                "packages": {"": {"name": "niko-sdlc-integration-target", "version": "1.0.0"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (target / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    manifest = {
        "version": "target_profile_manifest_v1",
        "project_profile_id": "typescript_fullstack_v1",
        "project_profile_schema": "project_profile_v1",
        "language_profile_id": "typescript_v1",
        "language_profile_schema": "language_profile_v1",
        "components": [
            {"component_id": component_id, "editable_roots": paths}
            for component_id, paths in editable_roots.items()
        ],
        "protected_paths": protected_paths,
        "required_paths": [
            ".sdlc/target-profile.json",
            "package.json",
            "package-lock.json",
            "compose.yaml",
            "apps/web",
            "apps/api",
            "packages/contracts",
            "packages/db",
            "tests/e2e",
        ],
        "verification_stage_ids": verification_stage_ids,
    }
    manifest_path = target / ".sdlc/target-profile.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    remote.mkdir()
    _git(remote, "init", "--bare")
    _git(target, "init")
    _git(target, "config", "user.name", "Niko Integration")
    _git(target, "config", "user.email", "niko-integration@example.com")
    _git(target, "add", ".")
    _git(target, "commit", "-m", "initial")
    _git(target, "branch", "-M", "main")
    _git(target, "remote", "add", "origin", str(remote))
    _git(target, "push", "-u", "origin", "main")
    return target, remote


class _IntegrationGraph:
    """Small app shell that preserves Niko turn selection without invoking chat LLMs."""

    def __init__(self) -> None:
        self.trace_logger = object()
        self.finished: list[tuple[str, str, str, str, dict | None]] = []

    def conversation_id_for(self, gateway_message) -> str:
        return str(gateway_message.chat_id)

    def start_chat_turn(self, conversation_id, prompt, gateway_message):
        return SimpleNamespace(turn_id=f"trace-{len(self.finished) + 1}")

    def decide_two_agent_route(self, prompt, conversation_id, trace_id):
        return SimpleNamespace(kind="deep_agent", reason="integration")

    def handle_single_agent_message(self, *args, **kwargs):  # pragma: no cover - SDLC owns the turn
        raise AssertionError("SDLC smoke unexpectedly reached normal chat")

    def handle_busy_reply(self, *args, **kwargs):  # pragma: no cover - SDLC owns the turn
        raise AssertionError("SDLC smoke unexpectedly reached busy reply")

    def handle_two_agent_message(self, *args, **kwargs):  # pragma: no cover - SDLC owns the turn
        raise AssertionError("SDLC smoke unexpectedly reached the chat graph")

    def finish_workflow_reply(
        self,
        conversation_id,
        reply,
        gateway_message,
        deliver_reply,
        *,
        route,
        trace_id,
        meta=None,
    ):
        self.finished.append((conversation_id, reply, route, trace_id, meta))
        deliver_reply(reply)
        return route

    def finish_background_workflow_reply(self, *args, **kwargs):  # pragma: no cover - not used here
        raise AssertionError("Synchronous checkpoint smoke unexpectedly queued a background job")


@unittest.skipUnless(
    (SDLC_ROOT / "sdlc_harness" / "interfaces" / "http.py").exists(),
    "Sibling SDLC_Harness checkout is required for the cutover process smoke.",
)
class SdlcApiProcessIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.runtime_root = self.temp_path / "sdlc-runtime"
        self.stderr_path = self.temp_path / "sdlc-api.stderr.log"
        self.stderr_file = self.stderr_path.open("w+b")
        self.port = self._free_port()
        self.token = "slice-8-process-token"
        self.target_repo, self.target_remote = _create_typescript_target(self.temp_path)

        server_env = os.environ.copy()
        server_env.update(
            {
                "SDLC_API_TOKEN": self.token,
                "SDLC_LLM_MODE": "fake",
                "SDLC_SYNC_MODE": "mock",
                "SDLC_RUNTIME_CONFIG_FILE": str(self.runtime_root / "config.json"),
                "SDLC_API_SHUTDOWN_TIMEOUT_SECONDS": "2",
                "SDLC_PROJECT_PROFILE": "typescript_fullstack_v1",
                "SDLC_TARGET_REPO_PATH": str(self.target_repo),
                "SDLC_TARGET_REPO_REMOTE": str(self.target_remote.resolve()),
                "SDLC_TARGET_WORKTREE_ROOT": str(self.temp_path / "worktrees"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sdlc_harness.interfaces.http",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--runtime-root",
                str(self.runtime_root),
            ],
            cwd=SDLC_ROOT,
            env=server_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_file,
        )
        self._wait_for_health()

        self.environment = patch.dict(
            os.environ,
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_SDLC_API_BASE_URL": f"http://127.0.0.1:{self.port}",
                "NIKO_SDLC_API_TOKEN": self.token,
                "NIKO_SDLC_API_REQUEST_TIMEOUT_SECONDS": "2",
                "NIKO_SDLC_API_OPERATION_TIMEOUT_SECONDS": "30",
                "NIKO_SDLC_API_POLL_INTERVAL_SECONDS": "0.01",
                "NIKO_SDLC_SYNC_MODE": "mock",
                "NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": "0",
                "NIKO_RUNTIME_CONFIG_FILE": str(self.temp_path / "niko-config.json"),
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        self.stderr_file.close()
        self._temp_dir.cleanup()

    def test_niko_start_status_and_requirement_approval_use_real_api_only(self) -> None:
        graph = _IntegrationGraph()
        app = NikoApp(chat_graph=graph)
        message = SimpleNamespace(chat_id="slice-8-chat", user=SimpleNamespace(key="telegram:slice-8"))
        delivered: list[str] = []

        route = app.handle_message("Niko thực hiện eTour UC1", message, delivered.append)
        self.assertEqual(route, ROUTE_SDLC_START)
        run_id = str(graph.finished[-1][4]["run_id"])
        self.assertRegex(run_id, r"^run_\d{8}_\d{6}_[a-f0-9]+$")

        route = app.handle_message(f"status {run_id}", message, delivered.append)
        self.assertEqual(route, ROUTE_SDLC_STATUS)

        route = app.handle_message(f"duyệt {run_id}", message, delivered.append)
        self.assertEqual(route, ROUTE_SDLC_APPROVE)

        state = json.loads((self.runtime_root / "runs" / run_id / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "waiting_design_approval")
        self.assertEqual(state["last_sync"]["status"], "mock_synced")
        operation_files = list((self.runtime_root / "operations").glob("*.json"))
        self.assertEqual(len(operation_files), 2)
        commands = {
            json.loads(path.read_text(encoding="utf-8"))["command"] for path in operation_files
        }
        self.assertEqual(commands, {"start_or_get_run", "approve_checkpoint"})
        self.assertTrue(any("waiting_design_approval" in reply for reply in delivered))

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + 10
        url = f"http://127.0.0.1:{self.port}/api/v1/health"
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                self.fail(f"SDLC API exited during startup: {self._server_stderr()}")
            request = Request(url, headers={"Authorization": f"Bearer {self.token}"})
            try:
                with urlopen(request, timeout=0.5) as response:
                    if response.status == 200:
                        return
            except (OSError, URLError):
                time.sleep(0.05)
        self.fail(f"SDLC API did not become healthy: {self._server_stderr()}")

    def _server_stderr(self) -> str:
        self.stderr_file.flush()
        return self.stderr_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
