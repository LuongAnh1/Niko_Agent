"""Policy preflight cho các lệnh điều khiển SDLC trước khi gọi CLI ghi."""

from __future__ import annotations

from dataclasses import dataclass

from niko.sdlc.client import (
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_SYNC,
    SdlcResult,
)


PHASE4_STATUSES = {
    "implementation_ready",
    "implementation_writing",
    "implementation_written",
    "implementation_testing",
    "implementation_tested",
    "implementation_repair_needed",
    "implementation_repair_blocked",
    "implementation_traced",
    "qa_testing",
    "qa_repair_needed",
    "qa_tested",
    "code_review_publishing",
    "code_review_publish_failed",
    "code_review_published",
    "code_review_syncing",
    "code_review_sync_failed",
    "code_review_ready",
    "code_review_approval_syncing",
    "code_review_approval_sync_failed",
    "code_review_approved",
}

PHASE1_3_APPROVAL_STATUSES = {
    "waiting_requirement_approval",
    "waiting_design_approval",
    "design_review",
    "waiting_implementation_plan_review",
}

_CONTRACT_ACTIONS = {
    ROUTE_SDLC_APPROVE: "approve_checkpoint",
    ROUTE_SDLC_CONTINUE: "continue_run",
    ROUTE_SDLC_SYNC: "sync_run",
}

PREFLIGHT_STATUS_ERROR = "status_error"
PREFLIGHT_ACTION_NOT_ALLOWED = "action_not_allowed"
PREFLIGHT_PHASE4_REQUIRED = "phase4_required"
PREFLIGHT_CODE_REVIEW_REQUIRED = "code_review_required"
PREFLIGHT_BACKGROUND_PENDING = "background_execution_pending"
PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED = "code_review_approval_disabled"
PREFLIGHT_EXECUTE_APPROVAL = "execute_phase1_3_approval"


@dataclass(frozen=True)
class SdlcPreflight:
    """Kết quả policy thuần, không tự gọi write action của SDLC_Harness."""

    action: str
    status_result: SdlcResult
    allowed: bool
    execute_now: bool
    reason: str


def evaluate_sdlc_preflight(action: str, status_result: SdlcResult) -> SdlcPreflight:
    """Đánh giá action dựa hoàn toàn trên status contract của SDLC_Harness."""

    if not status_result.ok:
        return SdlcPreflight(action, status_result, False, False, PREFLIGHT_STATUS_ERROR)

    allowed_actions = {str(item) for item in status_result.allowed_next_actions if item}
    status = (status_result.status or status_result.current_phase or "").strip()
    contract_action = _CONTRACT_ACTIONS.get(action, action)
    if contract_action not in allowed_actions:
        return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)

    if action == ROUTE_SDLC_CONTINUE:
        if status not in PHASE4_STATUSES:
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_PHASE4_REQUIRED)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_SYNC:
        if not status.startswith("code_review_"):
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_CODE_REVIEW_REQUIRED)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_APPROVE:
        checkpoint = status_result.pending_checkpoint if isinstance(status_result.pending_checkpoint, dict) else {}
        checkpoint_type = str(checkpoint.get("type") or "").strip()
        if status == "code_review_ready":
            if checkpoint_type == "code_review":
                return SdlcPreflight(
                    action,
                    status_result,
                    True,
                    False,
                    PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED,
                )
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)
        if status in PHASE1_3_APPROVAL_STATUSES:
            return SdlcPreflight(action, status_result, True, True, PREFLIGHT_EXECUTE_APPROVAL)
        return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)

    return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)


def preflight_notice(preflight: SdlcPreflight) -> str:
    """Giải thích ngắn gọn vì sao Niko chưa dispatch write action."""

    run_id = preflight.status_result.run_id
    run_label = f" `{run_id}`" if run_id else " này"
    if preflight.reason == PREFLIGHT_BACKGROUND_PENDING:
        action_label = "chạy tiếp Phase 4" if preflight.action == ROUTE_SDLC_CONTINUE else "đồng bộ lại"
        return (
            f"Dạ em đã kiểm tra run{run_label}. Lệnh {action_label} hiện chưa được bật trong Telegram, "
            "nên em chưa thay đổi run."
        )
    if preflight.reason == PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED:
        return (
            f"Dạ em chưa duyệt Code Review cho run{run_label}. Chức năng chuyển Draft PR sang "
            "Ready for review hiện chưa được bật trong Telegram."
        )
    if preflight.reason == PREFLIGHT_PHASE4_REQUIRED:
        return f"Dạ run{run_label} chưa ở trạng thái Phase 4 có thể chạy tiếp; em chưa thay đổi run."
    if preflight.reason == PREFLIGHT_CODE_REVIEW_REQUIRED:
        return f"Dạ run{run_label} chưa ở trạng thái Code Review có thể đồng bộ lại; em chưa thay đổi run."
    if preflight.reason == PREFLIGHT_ACTION_NOT_ALLOWED:
        return f"Dạ trạng thái hiện tại của run{run_label} không cho phép lệnh này; em chưa thay đổi run."
    return ""
