"""Small JSON CLI that behaves like the SDLC_Harness control surface.

The fixture never calls a network service. Tests provide all state and marker
paths through environment variables so the tracked fixture directory stays
read only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any


def _fixture_path(name: str, default_name: str) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else Path.cwd() / ".runtime" / default_name


def _option(argv: list[str], name: str) -> str:
    try:
        return argv[argv.index(name) + 1]
    except (ValueError, IndexError):
        return ""


def _read_state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append_command(command: str, run_id: str, argv: list[str]) -> None:
    path = _fixture_path("SDLC_SMOKE_COMMAND_LOG", "commands.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"command": command, "run_id": run_id, "argv": argv}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _wait_for_continue_release() -> bool:
    started_raw = os.environ.get("SDLC_SMOKE_CONTINUE_STARTED", "").strip()
    release_raw = os.environ.get("SDLC_SMOKE_CONTINUE_RELEASE", "").strip()
    started = Path(started_raw) if started_raw else _fixture_path("", "continue.started")
    release = Path(release_raw) if release_raw else _fixture_path("", "continue.release")
    started.parent.mkdir(parents=True, exist_ok=True)
    started.touch()

    timeout = max(0.1, float(os.environ.get("SDLC_SMOKE_CONTINUE_WAIT_SECONDS", "900")))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if release.exists():
            return True
        time.sleep(0.02)
    return release.exists()


def _error(run_id: str, error_code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "run_id": run_id, "error_code": error_code, "message": message}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    run_id = _option(args, "--run-id")
    _append_command(command, run_id, args)

    state_path = _fixture_path("SDLC_SMOKE_STATE_FILE", "state.json")
    state = _read_state(state_path)
    runs = state.setdefault("runs", {})

    if command == "get-status":
        payload = runs.get(run_id) or _error(run_id, "run_not_found", f"Run not found: {run_id}")
    elif command == "continue-run":
        if not _wait_for_continue_release():
            payload = _error(run_id, "fixture_continue_timeout", "Continue release marker was not created.")
        else:
            payload = state.get("continue_results", {}).get(run_id) or _error(
                run_id, "run_not_continuable", f"Run cannot continue: {run_id}"
            )
            if payload.get("ok"):
                runs[run_id] = payload
                _write_state(state_path, state)
    elif command == "sync-run":
        payload = state.get("sync_results", {}).get(run_id) or _error(
            run_id, "sync_not_ready", f"Run cannot sync: {run_id}"
        )
        if payload.get("ok"):
            runs[run_id] = payload
            _write_state(state_path, state)
    elif command == "approve-checkpoint":
        payload = state.get("approval_results", {}).get(run_id) or _error(
            run_id, "checkpoint_not_current", f"Checkpoint cannot be approved: {run_id}"
        )
        if payload.get("ok"):
            runs[run_id] = payload
            _write_state(state_path, state)
    else:
        payload = _error(run_id, "fixture_command_not_supported", f"Unsupported command: {command}")

    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "sdlc_fixture_error",
                    "message": f"Fixture failed: {type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)
