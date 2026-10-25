"""HTTP API client từ Niko sang SDLC_Harness.

Niko giữ hội thoại và điều phối trạng thái trước người dùng. SDLC_Harness sở hữu
source loading, artifact, checkpoint, workflow state và tool sync. Module này chỉ
gửi HTTP JSON, theo dõi operation bền vững và chuẩn hóa response cho Niko.

Mọi write request truyền rõ `NIKO_SDLC_SYNC_MODE`; mặc định là `mock`. Khi submit
có kết quả chưa xác định, client không tạo operation mới bằng một đường khác.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from niko.config import env_flag, env_value
from niko.harness.runtime_log import default_runtime_logger


DEFAULT_SDLC_SYNC_MODE = "mock"
DEFAULT_SDLC_API_BASE_URL = "http://127.0.0.1:7788"
DEFAULT_SDLC_API_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_SDLC_API_OPERATION_TIMEOUT_SECONDS = 1800.0
DEFAULT_SDLC_API_POLL_INTERVAL_SECONDS = 2.0
MAX_SDLC_API_RESPONSE_BYTES = 2 * 1024 * 1024
_TERMINAL_OPERATION_STATUSES = frozenset({"succeeded", "failed", "interrupted"})
ROUTE_SDLC_START = "sdlc_start_or_get_run"
ROUTE_SDLC_START_JIRA = "sdlc_start_from_jira"
ROUTE_SDLC_STATUS = "sdlc_get_status"
ROUTE_SDLC_APPROVE = "sdlc_approve_checkpoint"
ROUTE_SDLC_ANSWER_CLARIFICATION = "sdlc_answer_clarification"
ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT = "sdlc_challenge_policy_default"
ROUTE_SDLC_CLARIFY_MORE = "sdlc_clarify_more"
ROUTE_SDLC_CONTINUE = "sdlc_continue_run"
ROUTE_SDLC_REVALIDATE = "sdlc_revalidate_implementation"
ROUTE_SDLC_REVALIDATE_PLAN = "sdlc_revalidate_implementation_plan"
ROUTE_SDLC_REVALIDATE_DESIGN = "sdlc_revalidate_design"
ROUTE_SDLC_REVALIDATE_REQUIREMENT = "sdlc_revalidate_requirement"
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
POLICY_DEFAULT_CHALLENGE_PATTERN = re.compile(
    r"^\s*(?:phản\s+đối\s+mặc\s+định|phan\s+doi\s+mac\s+dinh|challenge\s+policy\s+default)\s+"
    r"(?P<assumption_id>ASM-\d+)\s+(?:cho|for)\s+"
    r"(?P<run_id>run_\d{8}_\d{6}_[a-f0-9]+)\s*:\s*"
    r"(?P<instruction>\S(?:.*\S)?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
POLICY_DEFAULT_CHALLENGE_PREFIX_PATTERN = re.compile(
    r"^\s*(?:phản\s+đối\s+mặc\s+định|phan\s+doi\s+mac\s+dinh|challenge\s+policy\s+default)\b",
    re.IGNORECASE,
)
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
    "revalidate implementation",
    "chay kiem tra lai implementation",
    "chạy kiểm tra lại implementation",
    "kiem tra lai implementation",
    "kiểm tra lại implementation",
    "chay lai verification",
    "chạy lại verification",
    "kiem tra lai verification",
    "kiểm tra lại verification",
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
REVALIDATE_DESIGN_WORDS = (
    "revalidate design",
    "validate lai design",
    "validate lại design",
    "kiem tra lai design",
    "kiểm tra lại design",
    "ra soat lai design",
    "rà soát lại design",
)
REVALIDATE_REQUIREMENT_WORDS = (
    "revalidate requirement",
    "validate lai requirement",
    "validate lại requirement",
    "kiem tra lai requirement",
    "kiểm tra lại requirement",
    "ra soat lai requirement",
    "rà soát lại requirement",
)
CLARIFY_MORE_WORDS = (
    "clarify more",
    "clarify requirement more",
    "lam ro them requirement",
    "làm rõ thêm requirement",
    "lam ro lai requirement",
    "làm rõ lại requirement",
    "hoi them requirement",
    "hỏi thêm requirement",
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
    assumption_id: str = ""
    instruction: str = ""


@dataclass(frozen=True)
class SdlcResult:
    """Kết quả đã chuẩn hóa từ JSON transport của SDLC_Harness."""

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
    operation_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def sdlc_enabled() -> bool:
    """Công tắc V1 để tránh route SDLC bắt nhầm khi chưa cấu hình."""

    return env_flag("NIKO_SDLC_HARNESS_ENABLED", "0")


def detect_sdlc_request(prompt: str) -> SdlcRequest | None:
    """Nhận diện SDLC control intent theo precedence cố định và run_id rõ ràng."""

    text = str(prompt or "").strip()
    lowered = text.lower()
    policy_challenge_match = POLICY_DEFAULT_CHALLENGE_PATTERN.match(text)
    if policy_challenge_match:
        return SdlcRequest(
            action=ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT,
            run_id=policy_challenge_match.group("run_id"),
            assumption_id=policy_challenge_match.group("assumption_id").upper(),
            instruction=policy_challenge_match.group("instruction").strip(),
        )
    if POLICY_DEFAULT_CHALLENGE_PREFIX_PATTERN.match(text):
        run_match = RUN_ID_PATTERN.search(text)
        assumption_match = re.search(r"\bASM-\d+\b", text, re.IGNORECASE)
        _, separator, trailing = text.partition(":")
        return SdlcRequest(
            action=ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT,
            run_id=run_match.group(0) if run_match else "",
            assumption_id=assumption_match.group(0).upper() if assumption_match else "",
            instruction=trailing.strip() if separator else "",
        )

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

    if run_match and any(word in lowered for word in REVALIDATE_DESIGN_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_REVALIDATE_DESIGN, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in CLARIFY_MORE_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_CLARIFY_MORE, run_id=run_match.group(0))

    if run_match and any(word in lowered for word in REVALIDATE_REQUIREMENT_WORDS):
        return SdlcRequest(action=ROUTE_SDLC_REVALIDATE_REQUIREMENT, run_id=run_match.group(0))

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


class HttpSdlcTransport:
    """HTTP JSON transport với operation polling và idempotency an toàn."""

    def get_status(self, *, run_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/api/v1/runs/{quote(run_id, safe='')}")

    def create_run(
        self,
        *,
        source: dict[str, str],
        requested_by: str,
        event: str,
    ) -> dict[str, Any]:
        return self._submit_and_wait(
            path="/api/v1/runs",
            body={
                "source": source,
                "requested_by": requested_by or "niko",
                "sync_mode": _sync_mode(),
            },
            event=event,
        )

    def run_action(
        self,
        *,
        run_id: str,
        action: str,
        requested_by: str,
        event: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._submit_and_wait(
            path=f"/api/v1/runs/{quote(run_id, safe='')}/actions",
            body={
                "action": action,
                "requested_by": requested_by or "niko",
                "sync_mode": _sync_mode(),
                "arguments": arguments or {},
            },
            event=event,
            run_id=run_id,
        )

    def _submit_and_wait(
        self,
        *,
        path: str,
        body: dict[str, Any],
        event: str,
        run_id: str = "",
    ) -> dict[str, Any]:
        idempotency_key = f"niko-{uuid4().hex}"
        default_runtime_logger().event(
            "sdlc",
            event,
            "Goi SDLC_Harness qua HTTP API.",
            data={"transport": "api", "path": path, "run_id": run_id},
        )

        receipt: dict[str, Any] = {}
        for attempt in range(2):
            receipt = self._request_json(
                "POST",
                path,
                body=body,
                idempotency_key=idempotency_key,
            )
            if receipt.get("error_code") not in {
                "sdlc_api_request_timeout",
                "sdlc_api_unavailable",
            }:
                break
            if attempt == 0:
                default_runtime_logger().event(
                    "sdlc",
                    "sdlc_api_submit_retry",
                    "Thu lai SDLC API submit voi cung idempotency key.",
                    level="warning",
                    data={"transport": "api", "path": path, "run_id": run_id},
                )

        if receipt.get("error_code") in {"sdlc_api_request_timeout", "sdlc_api_unavailable"}:
            return _error_payload(
                "sdlc_api_submit_unknown",
                "Chua xac dinh duoc SDLC API da nhan tac vu hay chua; khong tao operation moi de tranh thuc thi trung.",
                f"path={path}; attempts=2",
            )
        if receipt.get("ok") is not True:
            return receipt

        operation_id = str(receipt.get("operation_id") or "").strip()
        if not operation_id:
            return _error_payload(
                "sdlc_invalid_json",
                "SDLC API receipt thieu operation_id.",
                f"path={path}",
            )
        default_runtime_logger().event(
            "sdlc",
            "sdlc_api_operation_accepted",
            "SDLC API da nhan operation.",
            data={
                "transport": "api",
                "operation_id": operation_id,
                "run_id": str(receipt.get("run_id") or run_id),
            },
        )
        return self._poll_operation(
            operation_id=operation_id,
            initial_run_id=str(receipt.get("run_id") or run_id),
        )

    def _poll_operation(self, *, operation_id: str, initial_run_id: str) -> dict[str, Any]:
        timeout_seconds = _api_operation_timeout_seconds()
        poll_interval = _api_poll_interval_seconds()
        deadline = time.monotonic() + timeout_seconds
        last_status = "queued"
        last_run_id = initial_run_id

        while True:
            operation = self._request_json(
                "GET",
                f"/api/v1/operations/{quote(operation_id, safe='')}",
            )
            if operation.get("error_code") not in {
                "sdlc_api_request_timeout",
                "sdlc_api_unavailable",
            }:
                if operation.get("ok") is not True:
                    operation.setdefault("operation_id", operation_id)
                    operation.setdefault("run_id", last_run_id)
                    return operation
                last_status = str(operation.get("operation_status") or "").strip()
                last_run_id = str(operation.get("run_id") or last_run_id)
                if last_status in _TERMINAL_OPERATION_STATUSES:
                    default_runtime_logger().event(
                        "sdlc",
                        "sdlc_api_operation_finished",
                        "SDLC API operation da ket thuc.",
                        level="info" if last_status == "succeeded" else "error",
                        data={
                            "transport": "api",
                            "operation_id": operation_id,
                            "operation_status": last_status,
                            "run_id": last_run_id,
                        },
                    )
                    if last_status == "succeeded":
                        result = operation.get("result")
                        if not isinstance(result, dict):
                            return _error_payload(
                                "sdlc_invalid_json",
                                "SDLC API operation thanh cong nhung thieu result object.",
                                f"operation_id={operation_id}",
                            )
                        payload = dict(result)
                        payload["operation_id"] = operation_id
                        return payload
                    error = operation.get("error")
                    if not isinstance(error, dict):
                        error = {
                            "ok": False,
                            "error_code": "sdlc_api_operation_failed",
                            "message": "SDLC API operation ket thuc nhung thieu error object.",
                        }
                    payload = dict(error)
                    payload["ok"] = False
                    payload.setdefault("run_id", last_run_id)
                    payload["operation_id"] = operation_id
                    payload["operation_status"] = last_status
                    return payload

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                default_runtime_logger().event(
                    "sdlc",
                    "sdlc_api_operation_pending",
                    "Het thoi gian theo doi nhung SDLC operation van co the dang chay.",
                    level="warning",
                    data={
                        "transport": "api",
                        "operation_id": operation_id,
                        "operation_status": last_status,
                        "run_id": last_run_id,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                return {
                    "ok": False,
                    "error_code": "sdlc_operation_pending",
                    "message": "SDLC operation van dang xu ly sau thoi gian theo doi cua Niko.",
                    "operation_id": operation_id,
                    "operation_status": last_status,
                    "run_id": last_run_id,
                }
            time.sleep(min(poll_interval, remaining))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        try:
            base_url = _api_base_url()
        except RuntimeError as exc:
            return _error_payload("sdlc_config_error", str(exc), "invalid_api_base_url")

        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        token = env_value("NIKO_SDLC_API_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = Request(f"{base_url}{path}", data=encoded, headers=headers, method=method)

        try:
            with urlopen(request, timeout=_api_request_timeout_seconds()) as response:
                raw = response.read(MAX_SDLC_API_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raw = exc.read(MAX_SDLC_API_RESPONSE_BYTES + 1)
            payload = _decode_api_json(raw)
            if payload is not None:
                return payload
            return _error_payload(
                "sdlc_api_http_error",
                f"SDLC API tra HTTP {exc.code} ma khong co JSON loi hop le.",
                f"status={exc.code}; response_bytes={len(raw)}",
            )
        except (TimeoutError, socket.timeout) as exc:
            return _error_payload(
                "sdlc_api_request_timeout",
                "SDLC API khong tra loi trong request timeout.",
                type(exc).__name__,
            )
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return _error_payload(
                    "sdlc_api_request_timeout",
                    "SDLC API khong tra loi trong request timeout.",
                    type(exc.reason).__name__,
                )
            return _error_payload(
                "sdlc_api_unavailable",
                "Khong ket noi duoc SDLC API.",
                type(exc.reason).__name__,
            )
        except OSError as exc:
            return _error_payload(
                "sdlc_api_unavailable",
                "Khong ket noi duoc SDLC API.",
                type(exc).__name__,
            )

        if len(raw) > MAX_SDLC_API_RESPONSE_BYTES:
            return _error_payload(
                "sdlc_invalid_json",
                "SDLC API response vuot gioi han kich thuoc.",
                f"response_bytes>{MAX_SDLC_API_RESPONSE_BYTES}",
            )
        payload = _decode_api_json(raw)
        if payload is None:
            return _error_payload(
                "sdlc_invalid_json",
                "SDLC API khong tra JSON object hop le.",
                f"response_bytes={len(raw)}",
            )
        return payload


class SdlcClient:
    """Facade nghiệp vụ API-only giữ contract ổn định cho Niko."""

    def __init__(
        self,
        *,
        transport: HttpSdlcTransport | None = None,
    ) -> None:
        self._transport = transport or HttpSdlcTransport()

    def start_or_get_run(self, *, dataset: str, use_case: str, requested_by: str) -> SdlcResult:
        payload = self._transport.create_run(
            source={"type": "dataset", "dataset": dataset, "use_case": use_case},
            requested_by=requested_by,
            event=ROUTE_SDLC_START,
        )
        return _result_from_payload(payload, ROUTE_SDLC_START)

    def start_from_jira(self, *, issue_key: str, requested_by: str) -> SdlcResult:
        payload = self._transport.create_run(
            source={"type": "jira", "issue_key": issue_key},
            requested_by=requested_by,
            event=ROUTE_SDLC_START_JIRA,
        )
        return _result_from_payload(payload, ROUTE_SDLC_START_JIRA)

    def get_status(self, *, run_id: str) -> SdlcResult:
        return _result_from_payload(
            self._transport.get_status(run_id=run_id),
            ROUTE_SDLC_STATUS,
        )

    def approve_checkpoint(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        payload = self._transport.run_action(
            run_id=run_id,
            action="approve_checkpoint",
            requested_by=requested_by,
            event=ROUTE_SDLC_APPROVE,
        )
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

        payload = self._transport.run_action(
            run_id=run_id,
            action="answer_clarification",
            requested_by=answered_by,
            event=ROUTE_SDLC_ANSWER_CLARIFICATION,
            arguments={
                "question_id": question_id,
                "answer": answer,
                "answered_by": answered_by,
            },
        )
        return _result_from_payload(payload, ROUTE_SDLC_ANSWER_CLARIFICATION)

    def challenge_policy_default(
        self,
        *,
        run_id: str,
        assumption_id: str,
        instruction: str,
        requested_by: str,
    ) -> SdlcResult:
        """Thu hồi một reviewed default rồi mở lại câu hỏi requirement nguồn."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="challenge_policy_default",
            requested_by=requested_by,
            event=ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT,
            arguments={
                "assumption_id": assumption_id,
                "instruction": instruction,
            },
        )
        return _result_from_payload(payload, ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT)

    def continue_run(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Chạy bước kế tiếp; Phase 4 có thể sync checkpoint theo mode cấu hình."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="continue_run",
            requested_by=requested_by,
            event=ROUTE_SDLC_CONTINUE,
        )
        return _result_from_payload(payload, ROUTE_SDLC_CONTINUE)

    def revalidate_implementation(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Chạy lại Verification Runner và Validator mà không sửa code."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="revalidate_implementation",
            requested_by=requested_by,
            event=ROUTE_SDLC_REVALIDATE,
        )
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE)

    def revalidate_implementation_plan(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Tạo version plan mới từ input đã khóa và chạy lại Validator Phase 3."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="revalidate_implementation_plan",
            requested_by=requested_by,
            event=ROUTE_SDLC_REVALIDATE_PLAN,
        )
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE_PLAN)

    def revalidate_design(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Chạy lại Design Validator từ Requirement V2 đã khóa."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="revalidate_design",
            requested_by=requested_by,
            event=ROUTE_SDLC_REVALIDATE_DESIGN,
        )
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE_DESIGN)

    def revalidate_requirement(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Chạy lại validator tất định cho Requirement V2 hiện tại."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="revalidate_requirement",
            requested_by=requested_by,
            event=ROUTE_SDLC_REVALIDATE_REQUIREMENT,
        )
        return _result_from_payload(payload, ROUTE_SDLC_REVALIDATE_REQUIREMENT)

    def clarify_more(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Yêu cầu SDLC mở thêm một lượt semantic clarification tối đa năm câu."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="clarify_more",
            requested_by=requested_by,
            event=ROUTE_SDLC_CLARIFY_MORE,
        )
        return _result_from_payload(payload, ROUTE_SDLC_CLARIFY_MORE)

    def sync_run(self, *, run_id: str, requested_by: str = "niko") -> SdlcResult:
        """Yêu cầu SDLC_Harness re-sync checkpoint hiện tại."""

        payload = self._transport.run_action(
            run_id=run_id,
            action="sync_run",
            requested_by=requested_by,
            event=ROUTE_SDLC_SYNC,
        )
        return _result_from_payload(payload, ROUTE_SDLC_SYNC)


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
        operation_id=str(payload.get("operation_id") or ""),
        raw=payload,
    )


def _api_base_url() -> str:
    value = env_value("NIKO_SDLC_API_BASE_URL", DEFAULT_SDLC_API_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("NIKO_SDLC_API_BASE_URL khong hop le.")
    return value


def _positive_float_env(name: str, default: float, *, minimum: float) -> float:
    raw = env_value(name, str(default)).strip()
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _api_request_timeout_seconds() -> float:
    return _positive_float_env(
        "NIKO_SDLC_API_REQUEST_TIMEOUT_SECONDS",
        DEFAULT_SDLC_API_REQUEST_TIMEOUT_SECONDS,
        minimum=0.1,
    )


def _api_operation_timeout_seconds() -> float:
    return _positive_float_env(
        "NIKO_SDLC_API_OPERATION_TIMEOUT_SECONDS",
        DEFAULT_SDLC_API_OPERATION_TIMEOUT_SECONDS,
        minimum=0.1,
    )


def _api_poll_interval_seconds() -> float:
    return _positive_float_env(
        "NIKO_SDLC_API_POLL_INTERVAL_SECONDS",
        DEFAULT_SDLC_API_POLL_INTERVAL_SECONDS,
        minimum=0.01,
    )


def _sync_mode() -> str:
    value = env_value("NIKO_SDLC_SYNC_MODE", DEFAULT_SDLC_SYNC_MODE).strip().lower()
    return value if value in {"mock", "real", "both"} else DEFAULT_SDLC_SYNC_MODE


def _decode_api_json(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_SDLC_API_RESPONSE_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _error_payload(error_code: str, message: str, detail: str = "") -> dict[str, Any]:
    default_runtime_logger().event(
        "sdlc",
        "sdlc_error",
        message,
        level="error",
        data={"error_code": error_code, "detail": detail},
    )
    return {"ok": False, "error_code": error_code, "message": message}
