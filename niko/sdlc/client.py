"""Adapter CLI từ Niko sang SDLC_Harness.

Niko giữ vai trò hội thoại và điều phối trạng thái trước người dùng. Mọi việc
load source, tạo artifact, checkpoint và sync tool của workflow SDLC nằm ở repo
SDLC_Harness; file này chỉ gọi CLI và chuẩn hóa JSON trả về.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any

from niko.config import env_flag, env_value, resolve_project_path
from niko.harness.runtime_log import default_runtime_logger


DEFAULT_SDLC_HARNESS_WORKDIR = "../SDLC_Harness"
DEFAULT_SDLC_HARNESS_PYTHON_COMMAND = "python"
DEFAULT_SDLC_HARNESS_TIMEOUT_SECONDS = 120
DEFAULT_SDLC_HARNESS_LLM_MODE = "fake"
ROUTE_SDLC_START = "sdlc_start_or_get_run"
ROUTE_SDLC_START_JIRA = "sdlc_start_from_jira"
ROUTE_SDLC_STATUS = "sdlc_get_status"
ROUTE_SDLC_APPROVE = "sdlc_approve_checkpoint"

RUN_ID_PATTERN = re.compile(r"\brun_\d{8}_\d{6}_[a-f0-9]+\b", re.IGNORECASE)
UC1_PATTERN = re.compile(r"\b(?:uc\s*1|uc1)\b", re.IGNORECASE)
ETOUR_PATTERN = re.compile(r"\betour\b", re.IGNORECASE)
JIRA_ISSUE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b", re.IGNORECASE)
STATUS_WORDS = ("status", "trang thai", "trạng thái", "đến đâu", "den dau", "tien do", "tiến độ")
START_WORDS = ("thuc hien", "thực hiện", "chay", "chạy", "lam", "làm", "xu ly", "xử lý")
APPROVE_WORDS = (
    "duyet",
    "duyệt",
    "approve",
    "chap nhan",
    "chấp nhận",
    "dong y",
    "đồng ý",
    "xac nhan",
    "xác nhận",
)


@dataclass(frozen=True)
class SdlcRequest:
    """Yêu cầu SDLC V1 đã nhận diện từ prompt."""

    action: str
    dataset: str = "eTour"
    use_case: str = "UC1"
    issue_key: str = ""
    run_id: str = ""


@dataclass(frozen=True)
class SdlcResult:
    """Kết quả đã chuẩn hóa từ JSON CLI SDLC_Harness."""

    ok: bool
    action: str
    run_id: str = ""
    source_ref: str = ""
    current_phase: str = ""
    status: str = ""
    summary_for_user: str = ""
    pending_checkpoint: dict[str, Any] = field(default_factory=dict)
    allowed_next_actions: list[str] = field(default_factory=list)
    latest_artifacts: dict[str, Any] = field(default_factory=dict)
    quality_reports: dict[str, Any] = field(default_factory=dict)
    last_sync: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def sdlc_enabled() -> bool:
    """Công tắc V1 để tránh route SDLC bắt nhầm khi chưa cấu hình."""

    return env_flag("NIKO_SDLC_HARNESS_ENABLED", "0")


def detect_sdlc_request(prompt: str) -> SdlcRequest | None:
    """Nhận diện lát cắt V1: start/status/approve theo run_id rõ ràng."""

    text = str(prompt or "").strip()
    lowered = text.lower()
    run_match = RUN_ID_PATTERN.search(text)
    if run_match and any(word in lowered for word in APPROVE_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_APPROVE, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in STATUS_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_STATUS, run_id=run_match.group(0))

    issue_match = JIRA_ISSUE_PATTERN.search(text)
    if issue_match and any(word in lowered for word in START_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_START_JIRA, issue_key=issue_match.group(0).upper())

    if UC1_PATTERN.search(text) and (ETOUR_PATTERN.search(text) or any(word in lowered for word in START_WORDS)):
        return SdlcRequest(action=ROUTE_SDLC_START)

    return None


class SdlcClient:
    """Client subprocess tối giản cho CLI JSON của SDLC_Harness."""

    def start_or_get_run(self, *, dataset: str, use_case: str, requested_by: str) -> SdlcResult:
        args = [
            "start-or-get-run",
            "--dataset",
            dataset,
            "--use-case",
            use_case,
            "--requested-by",
            requested_by,
        ]
        _append_llm_mode(args)
        payload = self._run_json(
            args,
            event=ROUTE_SDLC_START,
        )
        return _result_from_payload(payload, ROUTE_SDLC_START)

    def start_from_jira(self, *, issue_key: str, requested_by: str) -> SdlcResult:
        args = [
            "start-from-jira",
            "--issue-key",
            issue_key,
            "--requested-by",
            requested_by,
        ]
        _append_llm_mode(args)
        payload = self._run_json(
            args,
            event=ROUTE_SDLC_START_JIRA,
        )
        return _result_from_payload(payload, ROUTE_SDLC_START_JIRA)

    def get_status(self, *, run_id: str) -> SdlcResult:
        payload = self._run_json(["get-status", "--run-id", run_id], event=ROUTE_SDLC_STATUS)
        return _result_from_payload(payload, ROUTE_SDLC_STATUS)

    def approve_checkpoint(self, *, run_id: str) -> SdlcResult:
        args = ["approve-checkpoint", "--run-id", run_id]
        _append_llm_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_APPROVE)
        return _result_from_payload(payload, ROUTE_SDLC_APPROVE)

    def _run_json(self, args: list[str], *, event: str) -> dict[str, Any]:
        try:
            command = [*_python_command(), "-m", "sdlc_harness.cli", *args]
            workdir = _sdlc_workdir()
            timeout_seconds = _timeout_seconds()
            default_runtime_logger().event(
                "sdlc",
                event,
                f"Goi SDLC_Harness: {args[0]}",
                data={
                    "workdir": str(workdir),
                    "command": _safe_command_for_log(command),
                    "timeout_seconds": timeout_seconds,
                },
            )
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except RuntimeError as exc:
            return _error_payload("sdlc_config_error", str(exc), str(exc))
        except subprocess.TimeoutExpired as exc:
            return _error_payload("sdlc_timeout", f"SDLC_Harness chay qua lau: {timeout_seconds}s", str(exc))
        except FileNotFoundError as exc:
            return _error_payload("sdlc_command_not_found", f"Khong tim thay command SDLC: {command[0]}", str(exc))
        except OSError as exc:
            return _error_payload("sdlc_process_error", f"Khong chay duoc SDLC_Harness: {exc}", str(exc))

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "SDLC_Harness tra loi loi khong ro."
            return _error_payload("sdlc_cli_failed", message, completed.stderr)

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return _error_payload("sdlc_invalid_json", "SDLC_Harness khong tra JSON hop le.", completed.stdout)

        if not isinstance(payload, dict):
            return _error_payload("sdlc_invalid_json", "SDLC_Harness JSON khong phai object.", completed.stdout)
        return payload


def _result_from_payload(payload: dict[str, Any], action: str) -> SdlcResult:
    return SdlcResult(
        ok=bool(payload.get("ok")),
        action=action,
        run_id=str(payload.get("run_id") or ""),
        source_ref=str(payload.get("source_ref") or ""),
        current_phase=str(payload.get("current_phase") or ""),
        status=str(payload.get("status") or ""),
        summary_for_user=str(payload.get("summary_for_user") or payload.get("message") or ""),
        pending_checkpoint=_dict_value(payload.get("pending_checkpoint")),
        allowed_next_actions=[str(item) for item in payload.get("allowed_next_actions") or []],
        latest_artifacts=_dict_value(payload.get("latest_artifacts")),
        quality_reports=_dict_value(payload.get("quality_reports")),
        last_sync=_dict_value(payload.get("last_sync")),
        error_code=str(payload.get("error_code") or ""),
        message=str(payload.get("message") or ""),
        raw=payload,
    )


def _python_command() -> list[str]:
    raw_command = env_value("SDLC_HARNESS_PYTHON_COMMAND", DEFAULT_SDLC_HARNESS_PYTHON_COMMAND).strip()
    args = shlex.split(raw_command, posix=False)
    if args:
        args[0] = shutil.which(args[0]) or args[0]
    return args or [DEFAULT_SDLC_HARNESS_PYTHON_COMMAND]


def _sdlc_workdir() -> Path:
    raw_path = env_value("SDLC_HARNESS_WORKDIR", DEFAULT_SDLC_HARNESS_WORKDIR).strip() or DEFAULT_SDLC_HARNESS_WORKDIR
    path = resolve_project_path(raw_path)
    if not path.exists():
        raise RuntimeError(f"Khong tim thay SDLC_Harness workdir: {path}")
    return path


def _timeout_seconds() -> int:
    raw_value = env_value("SDLC_HARNESS_TIMEOUT_SECONDS", str(DEFAULT_SDLC_HARNESS_TIMEOUT_SECONDS)).strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_SDLC_HARNESS_TIMEOUT_SECONDS


def _append_llm_mode(args: list[str]) -> None:
    raw_mode = env_value("SDLC_HARNESS_LLM_MODE", DEFAULT_SDLC_HARNESS_LLM_MODE).strip()
    if raw_mode:
        args.extend(["--llm-mode", raw_mode])


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_command_for_log(command: list[str]) -> list[str]:
    return [str(part) for part in command[:6]]


def _error_payload(error_code: str, message: str, detail: str = "") -> dict[str, Any]:
    default_runtime_logger().event(
        "sdlc",
        "sdlc_error",
        message,
        level="error",
        data={"error_code": error_code, "detail": detail},
    )
    return {"ok": False, "error_code": error_code, "message": message}
