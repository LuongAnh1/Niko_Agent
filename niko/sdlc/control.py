"""Policy preflight trước khi Niko gửi write action tới SDLC HTTP API."""

from __future__ import annotations

from dataclasses import dataclass

from niko.config import env_flag
from niko.sdlc.client import (
    ROUTE_SDLC_ANSWER_CLARIFICATION,
    ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT,
    ROUTE_SDLC_CLARIFY_MORE,
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_REVALIDATE,
    ROUTE_SDLC_REVALIDATE_DESIGN,
    ROUTE_SDLC_REVALIDATE_REQUIREMENT,
    ROUTE_SDLC_REVALIDATE_PLAN,
    ROUTE_SDLC_SYNC,
    SdlcResult,
)


PHASE4_STATUSES = {
    "implementation_ready",
    "implementation_writing",
    "implementation_executing",
    "implementation_paused",
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
    ROUTE_SDLC_ANSWER_CLARIFICATION: "answer_clarification",
    ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT: "challenge_policy_default",
    ROUTE_SDLC_CLARIFY_MORE: "clarify_more",
    ROUTE_SDLC_APPROVE: "approve_checkpoint",
    ROUTE_SDLC_CONTINUE: "continue_run",
    ROUTE_SDLC_REVALIDATE: "revalidate_implementation",
    ROUTE_SDLC_REVALIDATE_DESIGN: "revalidate_design",
    ROUTE_SDLC_REVALIDATE_REQUIREMENT: "revalidate_requirement",
    ROUTE_SDLC_REVALIDATE_PLAN: "revalidate_implementation_plan",
    ROUTE_SDLC_SYNC: "sync_run",
}

PREFLIGHT_STATUS_ERROR = "status_error"
PREFLIGHT_ACTION_NOT_ALLOWED = "action_not_allowed"
PREFLIGHT_PHASE4_REQUIRED = "phase4_required"
PREFLIGHT_BACKGROUND_PENDING = "background_execution_pending"
PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED = "code_review_approval_disabled"
PREFLIGHT_EXECUTE_APPROVAL = "execute_phase1_3_approval"
PREFLIGHT_QUESTION_NOT_CURRENT = "clarification_question_not_current"
PREFLIGHT_POLICY_DEFAULT_NOT_CURRENT = "policy_default_not_current"


def code_review_approval_enabled() -> bool:
    """Feature gate riêng cho thao tác GitHub Draft -> Ready từ Telegram."""

    return env_flag("NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED", "0")


@dataclass(frozen=True)
class SdlcPreflight:
    """Kết quả policy thuần, không tự gọi write action của SDLC_Harness."""

    action: str
    status_result: SdlcResult
    allowed: bool
    execute_now: bool
    reason: str


def evaluate_sdlc_preflight(
    action: str,
    status_result: SdlcResult,
    *,
    allow_code_review_approval: bool = False,
    question_id: str = "",
    assumption_id: str = "",
) -> SdlcPreflight:
    """Đánh giá action dựa hoàn toàn trên status contract của SDLC_Harness."""

    if not status_result.ok:
        return SdlcPreflight(action, status_result, False, False, PREFLIGHT_STATUS_ERROR)

    allowed_actions = {str(item) for item in status_result.allowed_next_actions if item}
    status = (status_result.status or status_result.current_phase or "").strip()
    contract_action = _CONTRACT_ACTIONS.get(action, action)
    if contract_action not in allowed_actions:
        return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)

    if action == ROUTE_SDLC_ANSWER_CLARIFICATION:
        checkpoint = status_result.pending_checkpoint if isinstance(status_result.pending_checkpoint, dict) else {}
        current_question = checkpoint.get("current_question")
        current_id = str(current_question.get("id") or "") if isinstance(current_question, dict) else ""
        if status != "waiting_requirement_clarification" or checkpoint.get("type") != "requirement_clarification":
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)
        if not question_id or current_id.casefold() != question_id.casefold():
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_QUESTION_NOT_CURRENT)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT:
        checkpoint = status_result.pending_checkpoint if isinstance(status_result.pending_checkpoint, dict) else {}
        active_ids = checkpoint.get("policy_default_assumption_ids")
        normalized_active_ids = {
            str(item).upper() for item in active_ids or [] if str(item).strip()
        } if isinstance(active_ids, list) else set()
        if status != "waiting_requirement_approval" or checkpoint.get("type") != "requirement_approval":
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)
        if not assumption_id or assumption_id.upper() not in normalized_active_ids:
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_POLICY_DEFAULT_NOT_CURRENT)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_CLARIFY_MORE:
        if status not in {"waiting_requirement_approval", "waiting_requirement_clarification_continue"}:
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_ACTION_NOT_ALLOWED)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_CONTINUE:
        if status == "waiting_requirement_clarification_continue":
            return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)
        phase = (status_result.current_phase or "").strip()
        recoverable_phase4_block = status == "blocked" and phase.startswith(("implementation", "qa_"))
        if status not in PHASE4_STATUSES and not recoverable_phase4_block:
            return SdlcPreflight(action, status_result, False, False, PREFLIGHT_PHASE4_REQUIRED)
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_REVALIDATE:
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_REVALIDATE_DESIGN:
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_REVALIDATE_REQUIREMENT:
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_REVALIDATE_PLAN:
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_SYNC:
        return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)

    if action == ROUTE_SDLC_APPROVE:
        checkpoint = status_result.pending_checkpoint if isinstance(status_result.pending_checkpoint, dict) else {}
        checkpoint_type = str(checkpoint.get("type") or "").strip()
        if status == "code_review_ready":
            if checkpoint_type == "code_review":
                if allow_code_review_approval:
                    return SdlcPreflight(action, status_result, True, False, PREFLIGHT_BACKGROUND_PENDING)
                return SdlcPreflight(
                    action,
                    status_result,
                    False,
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
        action_label = {
            ROUTE_SDLC_ANSWER_CLARIFICATION: "gửi câu trả lời làm rõ requirement",
            ROUTE_SDLC_CHALLENGE_POLICY_DEFAULT: "phản đối policy default",
            ROUTE_SDLC_CLARIFY_MORE: "mở thêm một lượt làm rõ requirement",
            ROUTE_SDLC_CONTINUE: "chạy tiếp workflow",
            ROUTE_SDLC_REVALIDATE: "chạy lại Verification Runner và Validator",
            ROUTE_SDLC_REVALIDATE_DESIGN: "tạo version mới và chạy lại Design Validator",
            ROUTE_SDLC_REVALIDATE_REQUIREMENT: "rà lại requirement từ source snapshot đã khóa",
            ROUTE_SDLC_REVALIDATE_PLAN: "tạo version mới và kiểm tra lại implementation plan",
            ROUTE_SDLC_SYNC: "đồng bộ lại",
            ROUTE_SDLC_APPROVE: "duyệt Code Review",
        }.get(preflight.action, "thực hiện")
        return (
            f"Dạ em đã kiểm tra run{run_label}. Lệnh {action_label} hiện chưa được bật trong Telegram, "
            "nên em chưa thay đổi run."
        )
    if preflight.reason == PREFLIGHT_QUESTION_NOT_CURRENT:
        checkpoint = preflight.status_result.pending_checkpoint
        question = checkpoint.get("current_question") if isinstance(checkpoint, dict) else None
        current_id = str(question.get("id") or "") if isinstance(question, dict) else ""
        current_label = f" Câu hiện tại là `{current_id}`." if current_id else ""
        return f"Dạ câu hỏi làm rõ của run{run_label} đã thay đổi hoặc không còn hiện hành.{current_label} Em chưa gửi câu trả lời."
    if preflight.reason == PREFLIGHT_POLICY_DEFAULT_NOT_CURRENT:
        checkpoint = preflight.status_result.pending_checkpoint
        active_ids = checkpoint.get("policy_default_assumption_ids") if isinstance(checkpoint, dict) else None
        active = [str(item) for item in active_ids or [] if str(item).strip()] if isinstance(active_ids, list) else []
        active_label = f" Các default hiện hành: {', '.join(f'`{item}`' for item in active)}." if active else ""
        return (
            f"Dạ policy default của run{run_label} đã thay đổi hoặc không còn hiện hành."
            f"{active_label} Em chưa gửi lệnh phản đối."
        )
    if preflight.reason == PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED:
        return (
            f"Dạ em chưa duyệt Code Review cho run{run_label}. Chức năng chuyển Draft PR sang "
            "Ready for review hiện chưa được bật trong Telegram."
        )
    if preflight.reason == PREFLIGHT_PHASE4_REQUIRED:
        return f"Dạ run{run_label} chưa ở trạng thái Phase 4 có thể chạy tiếp; em chưa thay đổi run."
    if preflight.reason == PREFLIGHT_ACTION_NOT_ALLOWED:
        return f"Dạ trạng thái hiện tại của run{run_label} không cho phép lệnh này; em chưa thay đổi run."
    return ""
