"""Adapter CLI từ Niko sang SDLC_Harness.

Niko giữ vai trò hội thoại và điều phối trạng thái trước người dùng. Mọi việc
load source, tạo artifact, checkpoint và sync tool của workflow SDLC nằm ở repo
SDLC_Harness; file này chỉ gọi CLI và chuẩn hóa JSON trả về.

Các lệnh có thể ghi Jira/Confluence luôn truyền `--sync-mode` từ
`NIKO_SDLC_SYNC_MODE`; mặc định `mock` để test Niko không vô tình ghi tool thật.
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
DEFAULT_SDLC_HARNESS_SYNC_MODE = "mock"
ROUTE_SDLC_START = "sdlc_start_or_get_run"
ROUTE_SDLC_START_JIRA = "sdlc_start_from_jira"
ROUTE_SDLC_STATUS = "sdlc_get_status"
ROUTE_SDLC_APPROVE = "sdlc_approve_checkpoint"
ROUTE_SDLC_ANSWER_CLARIFICATION = "sdlc_answer_clarification"
ROUTE_SDLC_CONTINUE = "sdlc_continue_run"
ROUTE_SDLC_REVALIDATE = "sdlc_revalidate_implementation"
ROUTE_SDLC_REVALIDATE_PLAN = "sdlc_revalidate_implementation_plan"
ROUTE_SDLC_SYNC = "sdlc_sync_run"

RUN_ID_PATTERN = re.compile(r"\brun_\d{8}_\d{6}_[a-f0-9]+\b", re.IGNORECASE)
USE_CASE_PATTERN = re.compile(r"\buc\s*(\d+)\b", re.IGNORECASE)
ETOUR_PATTERN = re.compile(r"\betour\b", re.IGNORECASE)
JIRA_ISSUE_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b", re.IGNORECASE)
CLARIFICATION_ANSWER_PATTERN = re.compile(
    r"^\s*(?:trả\s+lời|tra\s+loi)\s+"
    r"(?P<question_id>Q-\d+)\s+cho\s+"
    r"(?P<run_id>run_\d{8}_\d{6}_[a-f0-9]+)\s*:\s*"
    r"(?P<answer>\S(?:.*\S)?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
CLARIFICATION_PREFIX_PATTERN = re.compile(r"^\s*(?:trả\s+lời|tra\s+loi)\b", re.IGNORECASE)
STATUS_WORDS = ("status", "trang thai", "trạng thái", "đến đâu", "den dau", "tien do", "tiến độ")
START_WORDS = ("thuc hien", "thực hiện", "chay", "chạy", "lam", "làm", "xu ly", "xử lý")
SYNC_WORDS = ("dong bo lai", "đồng bộ lại", "sync lai", "sync lại", "resync", "retry sync")
CONTINUE_WORDS = (
    "chay tiep",
    "chạy tiếp",
    "tiep tuc",
    "tiếp tục",
    "continue",
    "lam tiep",
    "làm tiếp",
    "trien khai tiep",
    "triển khai tiếp",
)
REVALIDATE_WORDS = (
    "revalidate",
    "chay lai validator",
    "chạy lại validator",
    "validate lai",
    "validate lại",
    "kiem tra lai validator",
    "kiểm tra lại validator",
)
REVALIDATE_PLAN_WORDS = (
    "revalidate implementation plan",
    "revalidate plan",
    "rà soát lại implementation plan",
    "ra soat lai implementation plan",
    "tạo lại implementation plan",
    "tao lai implementation plan",
    "kiểm tra lại implementation plan",
    "kiem tra lai implementation plan",
)
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
    question_id: str = ""
    answer: str = ""


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
    last_error: dict[str, Any] = field(default_factory=dict)
    workspace: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def sdlc_enabled() -> bool:
    """Công tắc V1 để tránh route SDLC bắt nhầm khi chưa cấu hình."""

    return env_flag("NIKO_SDLC_HARNESS_ENABLED", "0")


def detect_sdlc_request(prompt: str) -> SdlcRequest | None:
    """Nhận diện SDLC control intent theo precedence cố định và run_id rõ ràng."""

    text = str(prompt or "").strip()
    lowered = text.lower()
    clarification_match = CLARIFICATION_ANSWER_PATTERN.match(text)
    if clarification_match:
        return SdlcRequest(
            action=ROUTE_SDLC_ANSWER_CLARIFICATION,
            run_id=clarification_match.group("run_id"),
            question_id=clarification_match.group("question_id").upper(),
            answer=clarification_match.group("answer").strip(),
        )
    if CLARIFICATION_PREFIX_PATTERN.match(text):
        return None

    run_match = RUN_ID_PATTERN.search(text)
    if run_match and any(word in lowered for word in REVALIDATE_PLAN_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_REVALIDATE_PLAN, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in REVALIDATE_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_REVALIDATE, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in APPROVE_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_APPROVE, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in SYNC_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_SYNC, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in CONTINUE_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_CONTINUE, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in STATUS_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_STATUS, run_id=run_match.group(0))

    issue_match = JIRA_ISSUE_PATTERN.search(text)
    if issue_match and any(word in lowered for word in START_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_START_JIRA, issue_key=issue_match.group(0).upper())

    use_case_match = USE_CASE_PATTERN.search(text)
    if use_case_match and (ETOUR_PATTERN.search(text) or any(word in lowered for word in START_WORDS)):
        return SdlcRequest(action=ROUTE_SDLC_START, use_case=f"UC{use_case_match.group(1)}")

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
        _append_sync_mode(args)
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
        _append_sync_mode(args)
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
        _append_sync_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_APPROVE)
        return _result_from_payload(payload, ROUTE_SDLC_APPROVE)

    def answer_clarification(
        self,
        *,
        run_id: str,
        question_id: str,
        answer: str,
        answered_by: str,
    ) -> SdlcResult:
        """Trả lời đúng câu hỏi requirement hiện hành của một run V2."""

        args = [
            "answer-clarification",
            "--run-id",
            run_id,
            "--question-id",
            question_id,
            "--answer",
            answer,
            "--answered-by",
            answered_by,
        ]
        _append_llm_mode(args)
        _append_sync_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_ANSWER_CLARIFICATION)
        return _result_from_payload(payload, ROUTE_SDLC_ANSWER_CLARIFICATION)

    def continue_run(self, *, run_id: str) -> SdlcResult:
        """Chạy bước kế tiếp; Phase 4 có thể sync checkpoint theo mode cấu hình."""

        args = ["continue-run", "--run-id", run_id]
        _append_llm_mode(args)
        _append_sync_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_CONTINUE)
        return _result_from_payload(payload, ROUTE_SDLC_CONTINUE)

    def revalidate_implementation(self, *, run_id: str) -> SdlcResult:
        """Chạy lại Verification Runner và Validator mà không sửa code."""

        args = ["revalidate-implementation", "--run-id", run_id]
        _append_llm_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_REVALIDATE)
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE)

    def revalidate_implementation_plan(self, *, run_id: str) -> SdlcResult:
        """Tạo version plan mới từ input đã khóa và chạy lại Validator Phase 3."""

        args = ["revalidate-implementation-plan", "--run-id", run_id]
        _append_llm_mode(args)
        _append_sync_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_REVALIDATE_PLAN)
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE_PLAN)

    def sync_run(self, *, run_id: str) -> SdlcResult:
        """Yêu cầu SDLC_Harness re-sync checkpoint hiện tại."""

        args = ["sync-run", "--run-id", run_id]
        _append_sync_mode(args)
        payload = self._run_json(args, event=ROUTE_SDLC_SYNC)
        return _result_from_payload(payload, ROUTE_SDLC_SYNC)

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

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            if completed.returncode != 0:
                return _error_payload(
                    "sdlc_cli_failed",
                    f"SDLC_Harness command failed with exit code {completed.returncode}.",
                    _process_output_summary(completed),
                )
            return _error_payload(
                "sdlc_invalid_json",
                "SDLC_Harness khong tra JSON hop le.",
                _process_output_summary(completed),
            )

        if not isinstance(payload, dict):
            error_code = "sdlc_cli_failed" if completed.returncode != 0 else "sdlc_invalid_json"
            message = (
                f"SDLC_Harness command failed with exit code {completed.returncode}."
                if completed.returncode != 0
                else "SDLC_Harness JSON khong phai object."
            )
            return _error_payload(error_code, message, _process_output_summary(completed))

        if completed.returncode != 0:
            if payload.get("ok") is False and str(payload.get("error_code") or "").strip():
                return payload
            return _error_payload(
                "sdlc_cli_failed",
                f"SDLC_Harness command failed with exit code {completed.returncode}.",
                _process_output_summary(completed),
            )
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
        last_error=_dict_value(payload.get("last_error")),
        workspace=_dict_value(payload.get("workspace")),
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


def _append_sync_mode(args: list[str]) -> None:
    """Niko truyền rõ sync mode; không để `.env` SDLC vô tình ghi tool thật."""

    raw_mode = env_value("NIKO_SDLC_SYNC_MODE", DEFAULT_SDLC_HARNESS_SYNC_MODE).strip().lower()
    if raw_mode not in {"mock", "real", "both"}:
        raw_mode = DEFAULT_SDLC_HARNESS_SYNC_MODE
    args.extend(["--sync-mode", raw_mode])


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_command_for_log(command: list[str]) -> list[str]:
    return [str(part) for part in command[:6]]


def _process_output_summary(completed: subprocess.CompletedProcess[str]) -> str:
    """Giữ metadata chẩn đoán mà không đưa raw stdout/stderr vào log hoặc reply."""

    return (
        f"exit_code={completed.returncode}; "
        f"stdout_chars={len(completed.stdout or '')}; "
        f"stderr_chars={len(completed.stderr or '')}"
    )


def _error_payload(error_code: str, message: str, detail: str = "") -> dict[str, Any]:
    default_runtime_logger().event(
        "sdlc",
        "sdlc_error",
        message,
        level="error",
        data={"error_code": error_code, "detail": detail},
    )
    return {"ok": False, "error_code": error_code, "message": message}
