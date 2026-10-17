"""Định dạng contract SDLC_Harness thành phản hồi ngắn, an toàn cho chat."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from niko.sdlc.client import ROUTE_SDLC_ANSWER_CLARIFICATION, ROUTE_SDLC_APPROVE, ROUTE_SDLC_STATUS, SdlcResult


_PHASE4_PASSED_VERIFICATION = {
    "implementation_tested",
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

_READY_FOR_REVIEW_STATES = {
    "code_review_approval_syncing",
    "code_review_approval_sync_failed",
    "code_review_approved",
}


def format_sdlc_reply(result: SdlcResult) -> str:
    """Đổi kết quả SDLC thành nội dung Telegram có thể hành động được."""

    if not result.ok:
        detail = _clip_text(result.message or "SDLC_Harness chưa xử lý được yêu cầu này.", 260)
        if result.error_code:
            return (
                f"Dạ SDLC_Harness đang lỗi `{_inline_code(result.error_code)}`.\n"
                f"Chi tiết: {detail}\n"
                "Em đã ghi lỗi vào log để mình soi tiếp."
            )
        return f"Dạ SDLC_Harness đang lỗi: {detail}"

    status_key = (result.status or result.current_phase or "").strip()
    status_label = _format_sdlc_status(
        result.current_phase,
        result.status,
        repair_source=_implementation_repair_source(result),
    )
    source_label = _format_sdlc_source(result.source_ref)
    sync_lines = _format_sync_targets(result.last_sync)
    failure_lines = _format_failure_reason(result)
    quality_lines = _format_quality_summary(result)
    developer_progress_lines = _format_developer_progress(result)
    action_lines = _format_action_suggestions(
        result.allowed_next_actions,
        status_key,
        run_id=result.run_id,
        pending_checkpoint=result.pending_checkpoint,
    )
    pr_url = _resolve_pr_url(result)

    if result.action == ROUTE_SDLC_STATUS:
        lines = ["Dạ em kiểm tra SDLC_Harness rồi anh."]
    elif result.action == ROUTE_SDLC_ANSWER_CLARIFICATION:
        lines = ["Dạ SDLC_Harness đã nhận câu trả lời làm rõ requirement rồi anh."]
    elif result.action == ROUTE_SDLC_APPROVE:
        lines = ["Dạ em đã gửi lệnh duyệt checkpoint sang SDLC_Harness rồi anh."]
    else:
        lines = ["Dạ SDLC_Harness đã nhận việc rồi anh."]

    if source_label:
        lines.append(f"Nguồn: {source_label}.")
    if status_label:
        lines.append(f"Trạng thái: {status_label}.")
    elif result.current_phase or result.status:
        status = " / ".join(part for part in (result.current_phase, result.status) if part)
        lines.append(f"Trạng thái: {_clip_text(status, 160)}.")

    summary = _format_sdlc_summary(result.summary_for_user)
    if summary:
        lines.extend(("", summary))

    pr_lines = _format_pr_and_workspace(result, pr_url, summary)
    if pr_lines:
        lines.append("")
        lines.extend(pr_lines)

    if sync_lines:
        lines.append("")
        lines.append(_format_sync_header(result.last_sync))
        lines.extend(sync_lines)

    if failure_lines:
        lines.append("")
        lines.extend(failure_lines)

    if developer_progress_lines:
        lines.extend(("", "Tiến độ Developer:"))
        lines.extend(developer_progress_lines)

    checkpoint = _format_checkpoint(result.pending_checkpoint)
    if checkpoint:
        lines.extend(("", checkpoint))

    if quality_lines:
        lines.extend(("", "Kiểm tra chất lượng:"))
        lines.extend(quality_lines)

    if action_lines:
        lines.extend(("", "Việc tiếp theo:"))
        lines.extend(action_lines)

    if result.run_id:
        lines.extend(("", f"Mã run: `{_inline_code(result.run_id)}`"))

    artifacts = _format_artifact_labels(result.latest_artifacts)
    if artifacts:
        lines.append(f"Artifact nội bộ: {artifacts}.")

    raw_status = _format_raw_status(result.current_phase, result.status)
    if raw_status and raw_status not in (status_label or ""):
        lines.append(f"Mã trạng thái: `{_inline_code(raw_status)}`")
    return "\n".join(lines)


def _format_sdlc_status(current_phase: str, status: str, *, repair_source: str = "") -> str:
    """Dịch phase/status kỹ thuật thành trạng thái dễ đọc trên Telegram."""

    status_key = (status or current_phase or "").strip()
    labels = {
        "requirement_drafting": "đang chuẩn hóa requirement",
        "waiting_requirement_clarification": "đang chờ anh trả lời câu hỏi làm rõ requirement",
        "waiting_requirement_approval": "đang chờ anh duyệt requirement trước khi sang design",
        "design_drafting": "đang tạo design artifact",
        "design_validating": "đang kiểm tra traceability và tính nhất quán của design",
        "design_revalidating": "đang chạy lại Design Validator hybrid",
        "design_repairing": "Designer đang sửa các blocker của design",
        "waiting_design_approval": "đang chờ anh review design checkpoint",
        "design_review": "đang chờ anh review design checkpoint",
        "implementation_planning": "đang lập implementation plan",
        "implementation_plan_revalidating": "đang tạo version mới và kiểm tra lại implementation plan",
        "waiting_implementation_plan_review": "đang chờ anh review implementation plan trước khi sang code/PR",
        "implementation_ready": "đã sẵn sàng bắt đầu triển khai code trong workspace của run",
        "implementation_writing": "đang viết và chỉnh sửa code trong workspace của run",
        "implementation_executing": "Developer đang triển khai một lát công việc trong workspace của run",
        "implementation_paused": "Developer đã tạm dừng an toàn và có thể tiếp tục lát công việc chưa xong",
        "implementation_written": "đã tạo code, đang chờ chạy verification",
        "implementation_testing": "đang chạy compile và unit verification",
        "implementation_tested": "verification đã pass, đang chờ kiểm tra traceability",
        "implementation_repair_needed": "Verification hoặc Validator yêu cầu Developer sửa code/test trước khi chạy tiếp",
        "implementation_repair_blocked": "repair implementation đang bị chặn và cần người review",
        "implementation_traced": "verification và traceability đã pass, đang chờ QA",
        "qa_testing": "đang chạy QA acceptance và system test",
        "qa_repair_needed": "QA phát hiện lỗi hành vi có thể repair",
        "qa_tested": "QA đã pass, đang chờ xuất bản Draft PR",
        "code_review_publishing": "đang tạo commit, branch và Draft PR",
        "code_review_publish_failed": "xuất bản Draft PR bị lỗi, có thể chạy tiếp để retry",
        "code_review_published": "Draft PR đã được xuất bản, đang chờ đồng bộ checkpoint",
        "code_review_syncing": "đang đồng bộ checkpoint Code Review lên Jira/Confluence",
        "code_review_sync_failed": "Draft PR và evidence đã được giữ; đồng bộ checkpoint đang lỗi",
        "code_review_ready": "Draft PR và evidence đã sẵn sàng để anh review",
        "code_review_approval_syncing": "PR đã chuyển sang Ready for review, đang đồng bộ kết quả duyệt",
        "code_review_approval_sync_failed": "PR vẫn ở Ready for review; đồng bộ kết quả duyệt đang lỗi",
        "code_review_approved": "PR đã Ready for review; anh tiếp tục review và merge trên GitHub",
        "blocked": "đang bị chặn, cần review trước khi chạy tiếp",
        "checkpoint_pending": "đang chờ duyệt checkpoint",
        "failed": "đang lỗi trong workflow SDLC",
        "done": "đã hoàn tất",
        "cancelled": "đã hủy",
    }
    if status_key == "implementation_repair_needed":
        if repair_source == "verification":
            labels[status_key] = "Verification yêu cầu Developer sửa code/test trước khi chạy tiếp"
        elif repair_source == "validator_code_trace":
            labels[status_key] = "Validator yêu cầu Developer sửa code/test trước khi chạy tiếp"
    label = labels.get(status_key)
    if not label and current_phase and status and current_phase != status:
        label = f"{current_phase} / {status}"
    return label or status_key


def _format_sdlc_source(source_ref: str) -> str:
    source = str(source_ref or "").strip()
    if source.startswith("jira_issue:"):
        return f"Jira issue `{_inline_code(source.split(':', 1)[1])}`"
    if source.startswith("dataset_use_case:"):
        parts = source.split(":")
        if len(parts) >= 3:
            return f"{_clip_text(parts[1], 60)} {_clip_text(parts[2], 60)}"
    if _looks_like_local_path(source):
        return ""
    return _clip_text(source, 140)


def _format_sdlc_summary(summary: str) -> str:
    cleaned = " ".join(str(summary or "").split())
    replacements = {
        "Đã chuẩn hóa jira": "Em đã chuẩn hóa Jira issue",
        "Đã chuẩn hóa eTour": "Em đã chuẩn hóa eTour",
        "validator đã pass": "validator đã kiểm tra xong",
        "sync status=synced": "đã đồng bộ lên tool",
        "Run đang chờ anh duyệt requirement trước khi chuyển sang design.": (
            "Hiện run đang chờ anh duyệt requirement trước khi chuyển sang design."
        ),
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return _clip_text(cleaned, 500)


def _format_sync_targets(last_sync: dict[str, Any]) -> list[str]:
    targets = last_sync.get("targets") if isinstance(last_sync, dict) else None
    if not isinstance(targets, list):
        return []

    lines: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        tool = _title_tool(str(target.get("tool") or "tool"))
        status = _clip_text(str(target.get("status") or "").strip(), 40)
        url = _safe_http_url(target.get("url"))
        error = str(target.get("error") or target.get("message") or "").strip()
        if url:
            if status and status not in {"synced", "mock_synced"}:
                lines.append(f"- {tool}: {status} - {url}")
            else:
                lines.append(f"- {tool}: {url}")
        elif status and error:
            lines.append(f"- {tool}: {status} ({_clip_text(error, 120)})")
        elif status:
            lines.append(f"- {tool}: {status}")
    return lines[:3]


def _format_sync_header(last_sync: dict[str, Any]) -> str:
    status = str(last_sync.get("status") or "").strip() if isinstance(last_sync, dict) else ""
    if status in {"partial_failed", "partially_failed", "partial_failure"}:
        return "Đồng bộ một phần:"
    if status in {"failed", "error", "sync_failed"}:
        return "Đồng bộ đang lỗi:"
    return "Đã đồng bộ:"


def _format_failure_reason(result: SdlcResult) -> list[str]:
    status = (result.status or result.current_phase or "").strip()
    is_failure = (
        status == "blocked"
        or status == "implementation_paused"
        or status.endswith("_failed")
        or status.endswith("_blocked")
    )
    if not is_failure:
        return []
    error = result.last_error if isinstance(result.last_error, dict) else {}
    code = str(error.get("error_code") or "").strip()
    message = str(error.get("message") or "").strip()
    if status == "implementation_paused":
        label = "Lý do tạm dừng"
    else:
        label = "Lý do chặn" if status == "blocked" or status.endswith("_blocked") else "Lý do lỗi"
    if code and message:
        return [f"{label}: `{_inline_code(code)}` - {_clip_text(message, 220)}"]
    if message:
        return [f"{label}: {_clip_text(message, 220)}"]
    if label == "Lý do chặn":
        return ["Lý do chặn: SDLC_Harness cần anh review artifact hoặc quality report trước khi chạy tiếp."]
    return []


def _format_developer_progress(result: SdlcResult) -> list[str]:
    raw = result.raw if isinstance(result.raw, dict) else {}
    progress = raw.get("developer_execution")
    if not isinstance(progress, dict) or not progress:
        return []
    completed = progress.get("completed_items")
    total = progress.get("total_items")
    remaining = progress.get("remaining_items")
    current = str(progress.get("current_item") or "").strip()
    lines: list[str] = []
    if isinstance(completed, int) and isinstance(total, int):
        line = f"- Lượt Developer: {completed}/{total} hoàn thành"
        if isinstance(remaining, int):
            line += f"; còn {remaining}"
        lines.append(line + ".")
    if current:
        lines.append(f"- Lượt triển khai hiện tại: `{_inline_code(_clip_text(current, 80))}`.")
    if result.status == "implementation_paused" and "continue_run" in result.allowed_next_actions:
        lines.append("- Journal đã giữ lượt triển khai đã hoàn thành; lần chạy tiếp tiếp tục từ task đang dở.")
    return lines[:4]


def _implementation_repair_source(result: SdlcResult) -> str:
    quality_reports = result.quality_reports if isinstance(result.quality_reports, dict) else {}
    repair = quality_reports.get("implementation_repair")
    if not isinstance(repair, dict):
        return ""
    return str(repair.get("feedback_source") or "").strip()


def _format_checkpoint(checkpoint: dict[str, Any]) -> str:
    if not isinstance(checkpoint, dict):
        return ""
    title = str(checkpoint.get("title") or checkpoint.get("name") or "").strip()
    artifact_ref = str(checkpoint.get("artifact_ref") or "").strip()
    checkpoint_type = str(checkpoint.get("type") or "").strip()
    if checkpoint_type == "requirement_clarification":
        current_question = checkpoint.get("current_question")
        if not isinstance(current_question, dict):
            return "Checkpoint đang chờ câu trả lời làm rõ requirement."
        question_id = str(current_question.get("id") or "").strip()
        question_text = _clip_text(str(current_question.get("text") or "").strip(), 260)
        remaining = checkpoint.get("remaining_blocking_count")
        label = f"`{_inline_code(question_id)}`" if question_id else "Câu hỏi hiện tại"
        line = f"Câu hỏi làm rõ requirement {label}: {question_text}" if question_text else f"Câu hỏi làm rõ requirement: {label}."
        if isinstance(remaining, int):
            line += f"\nSố câu hỏi blocking còn lại: {remaining}."
        return line
    if title:
        return f"Checkpoint cần anh duyệt: {_clip_text(title, 160)}."
    if checkpoint_type == "implementation_plan_review" and artifact_ref:
        return f"Checkpoint cần anh duyệt: implementation plan (`{_inline_code(artifact_ref)}`)."
    if checkpoint_type == "code_review":
        return "Checkpoint cần anh duyệt: Code Review."
    if artifact_ref and not _looks_like_local_path(artifact_ref):
        return f"Checkpoint cần anh duyệt: `{_inline_code(artifact_ref)}`."
    return ""


def _format_pr_and_workspace(result: SdlcResult, pr_url: str, summary: str) -> list[str]:
    lines: list[str] = []
    status = (result.status or result.current_phase or "").strip()
    if pr_url and pr_url not in summary:
        label = "PR Ready for review" if status in _READY_FOR_REVIEW_STATES else "Draft PR"
        lines.append(f"{label}: {pr_url}")

    workspace = result.workspace if isinstance(result.workspace, dict) else {}
    parts: list[str] = []
    branch = str(workspace.get("branch") or "").strip()
    if branch:
        parts.append(f"branch `{_inline_code(_clip_text(branch, 100))}`")
    head_sha = str(workspace.get("head_sha") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", head_sha):
        parts.append(f"HEAD `{head_sha[:12]}`")
    lifecycle = str(workspace.get("lifecycle") or "").strip()
    if lifecycle:
        parts.append(f"lifecycle `{_inline_code(_clip_text(lifecycle, 60))}`")
    pr_number = workspace.get("pr_number")
    if not pr_url and isinstance(pr_number, int):
        parts.append(f"PR `#{pr_number}`")
    if parts:
        lines.append("Workspace: " + "; ".join(parts) + ".")
    return lines


def _resolve_pr_url(result: SdlcResult) -> str:
    candidates: list[Any] = []
    if isinstance(result.pending_checkpoint, dict):
        candidates.append(result.pending_checkpoint.get("pull_request_url"))
    if isinstance(result.workspace, dict):
        candidates.append(result.workspace.get("pr_url"))
    if isinstance(result.quality_reports, dict):
        for key in ("code_review_publication", "code_review_approval"):
            report = result.quality_reports.get(key)
            if isinstance(report, dict):
                candidates.append(report.get("pr_url"))
    targets = result.last_sync.get("targets") if isinstance(result.last_sync, dict) else None
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, dict) and str(target.get("tool") or "").lower() == "github":
                candidates.append(target.get("url"))
    for candidate in candidates:
        url = _safe_http_url(candidate)
        if url:
            return url
    return ""


def _format_quality_summary(result: SdlcResult) -> list[str]:
    quality_reports = result.quality_reports
    if not isinstance(quality_reports, dict):
        return []

    status = (result.status or result.current_phase or "").strip()
    phase4 = any(
        key in quality_reports
        for key in ("implementation_traceability", "implementation_repair", "qa_acceptance", "code_review_publication", "code_review_approval")
    ) or status.startswith(("implementation_", "qa_", "code_review_"))
    if phase4:
        lines: list[str] = []
        has_test_report = isinstance(result.latest_artifacts, dict) and "test_report" in result.latest_artifacts
        verification = quality_reports.get("verification")
        if isinstance(verification, dict):
            verification_status = str(verification.get("status") or "").strip()
            verdict = "passed" if verification.get("ok") is True else verification_status or "chưa pass"
            details = [verdict]
            if verification.get("attempt") is not None:
                details.append(f"attempt={verification.get('attempt')}")
            if verification.get("repair_iteration") is not None:
                details.append(f"repair={verification.get('repair_iteration')}")
            if verification.get("repairable") is True:
                details.append("có thể giao Developer repair")
            reason = _clip_text(str(verification.get("reason") or ""), 120)
            line = "- Verification: " + "; ".join(details)
            lines.append(f"{line}; {reason}" if reason else line + ".")
        elif has_test_report and status in _PHASE4_PASSED_VERIFICATION:
            lines.append("- Verification: passed; test report đã có.")
        elif status in {"implementation_testing", "implementation_written"}:
            lines.append("- Verification: đang chờ kết quả compile/unit test.")

        trace = quality_reports.get("implementation_traceability")
        if isinstance(trace, dict):
            verdict = "passed" if trace.get("ok") is True else "chưa pass"
            stats = trace.get("stats") if isinstance(trace.get("stats"), dict) else {}
            mapping_count = trace.get("mapping_count", stats.get("mapping_count"))
            missing_count = trace.get("missing_count", stats.get("missing_count"))
            confidence = trace.get("llm_confidence")
            details = [verdict]
            if mapping_count is not None:
                details.append(f"mappings={mapping_count}")
            if missing_count is not None:
                details.append(f"missing={missing_count}")
            if confidence is not None:
                details.append(f"confidence={confidence}")
            summary = _clip_text(str(trace.get("summary") or ""), 120)
            line = "- Traceability: " + "; ".join(details)
            lines.append(f"{line}; {summary}" if summary else line + ".")

        qa = quality_reports.get("qa_acceptance")
        if isinstance(qa, dict):
            qa_status = str(qa.get("status") or ("passed" if qa.get("ok") is True else "chưa pass"))
            details = [_clip_text(qa_status, 40)]
            if qa.get("scenario_count") is not None:
                details.append(f"scenarios={qa.get('scenario_count')}")
            if qa.get("risk_count") is not None:
                details.append(f"risks={qa.get('risk_count')}")
            summary = _clip_text(str(qa.get("summary") or ""), 120)
            line = "- QA: " + "; ".join(details)
            lines.append(f"{line}; {summary}" if summary else line + ".")

        publication_key = "code_review_approval" if isinstance(quality_reports.get("code_review_approval"), dict) else "code_review_publication"
        publication = quality_reports.get(publication_key)
        if isinstance(publication, dict):
            state = "Ready for review" if publication_key == "code_review_approval" or publication.get("draft") is False else "Draft"
            commit_sha = str(publication.get("commit_sha") or "").strip()
            commit = f"; commit={commit_sha[:12]}" if re.fullmatch(r"[0-9a-fA-F]{7,64}", commit_sha) else ""
            lines.append(f"- GitHub: PR {state}{commit}.")

        repair = quality_reports.get("implementation_repair")
        if isinstance(repair, dict) and repair.get("needed") and len(lines) < 4:
            summary = _clip_text(str(repair.get("summary") or "cần repair trước khi chạy tiếp"), 150)
            lines.append(f"- Repair: {summary}")
        return [_clip_text(line, 180) for line in lines[:4]]

    lines = []
    plan_review = quality_reports.get("implementation_plan_review")
    if isinstance(plan_review, dict):
        summary = str(plan_review.get("summary") or "").strip()
        if summary:
            lines.append(f"- Validator plan: {_clip_text(summary, 180)}")
        if plan_review.get("repair_iterations") is not None:
            lines.append(f"- Implementation plan repair iterations: {plan_review.get('repair_iterations')}")
        if plan_review.get("issue_count") is not None:
            lines.append(f"- Implementation plan open issues: {plan_review.get('issue_count')}")
        return lines[:3]

    design_validation = quality_reports.get("design_validation")
    if isinstance(design_validation, dict):
        blockers = design_validation.get("blocking_count")
        warnings = design_validation.get("warning_count")
        status_label = "passed" if design_validation.get("ok") else "chưa pass"
        detail = f"- Design Validator: {status_label}"
        if isinstance(blockers, int):
            detail += f"; blockers={blockers}"
        if isinstance(warnings, int):
            detail += f"; warnings={warnings}"
        lines.append(detail)
        versions = design_validation.get("versions") or []
        if isinstance(versions, list) and versions:
            lines.append(f"- Design version: {versions[-1]}; repair iterations: {max(0, len(versions) - 1)}")
        return lines[:2]

    design_trace = quality_reports.get("design_traceability")
    if not isinstance(design_trace, dict):
        return []
    summary = str(design_trace.get("summary") or "").strip()
    if summary:
        lines.append(f"- Validator: {_clip_text(summary, 180)}")
    versions = design_trace.get("versions") or []
    if isinstance(versions, list) and versions:
        lines.append(f"- Design version: {versions[-1]}; repair iterations: {max(0, len(versions) - 1)}")
    return lines[:2]


def _format_action_suggestions(
    actions: list[str],
    status: str = "",
    *,
    run_id: str = "",
    pending_checkpoint: dict[str, Any] | None = None,
) -> list[str]:
    action_set = {str(action) for action in actions if action}
    if "answer_clarification" in action_set and status == "waiting_requirement_clarification":
        checkpoint = pending_checkpoint if isinstance(pending_checkpoint, dict) else {}
        question = checkpoint.get("current_question")
        question_id = str(question.get("id") or "") if isinstance(question, dict) else ""
        if question_id and run_id:
            return [f"- Trả lời bằng cú pháp: `Trả lời {question_id} cho {run_id}: <nội dung trả lời>`"]
        return ["- Anh trả lời câu hỏi làm rõ hiện tại để workflow cập nhật requirement."]
    if status == "code_review_approval_sync_failed":
        return ["- Retry đồng bộ cho đúng mã run; không chạy lại GitHub approval."]
    if status == "code_review_approved":
        return ["- Anh review PR và quyết định merge trực tiếp trên GitHub."]

    suggestions: list[str] = []
    if "revalidate_design" in action_set and status in {"waiting_design_approval", "blocked"}:
        suggestions.append("- Có thể yêu cầu em `revalidate design` kèm mã run để tạo version mới và chạy lại hybrid validator.")
    if "revalidate_implementation_plan" in action_set and status in {
        "waiting_implementation_plan_review",
        "blocked",
    }:
        suggestions.append("- Nếu plan còn lỗi hoặc xung đột với repo, yêu cầu em rà soát lại implementation plan kèm mã run.")
    if "approve_checkpoint" in action_set:
        if status == "code_review_ready":
            suggestions.append("- Anh review Draft PR và evidence của checkpoint Code Review.")
            suggestions.append("- Nếu ổn, duyệt checkpoint để chuyển PR sang Ready for review; merge vẫn do anh thực hiện trên GitHub.")
        elif status == "waiting_implementation_plan_review":
            suggestions.append("- Anh review implementation plan trên Jira/Confluence trước.")
            suggestions.append("- Nếu ổn, duyệt checkpoint để workflow chuẩn bị Phase 4 triển khai code.")
        elif status == "waiting_design_approval":
            suggestions.append("- Anh review design checkpoint trên Jira/Confluence trước.")
            suggestions.append("- Nếu ổn, nhắn em duyệt kèm mã run để mở bước tiếp theo khi workflow đã sẵn sàng.")
        else:
            suggestions.append("- Anh review requirement packet/checkpoint trước.")
            suggestions.append("- Nếu ổn, nhắn em duyệt kèm mã run để mở bước design.")
    if "sync_run" in action_set and not suggestions:
        suggestions.append("- Bước tiếp theo của workflow là retry đồng bộ cho đúng mã run.")
    if "continue_run" in action_set and not suggestions:
        if status == "implementation_paused":
            suggestions.append("- Chạy tiếp Phase 4 để tiếp tục lát công việc Developer chưa hoàn thành.")
        else:
            suggestions.append("- Bước tiếp theo của workflow là chạy tiếp Phase 4 cho mã run này.")
    if not suggestions and "get_status" in action_set:
        suggestions.append("- Anh có thể hỏi lại mã run để xem tiến độ.")
    return suggestions[:4]


def _format_artifact_labels(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        return ""
    labels = {
        "use_case_packet": "use case packet",
        "formal_use_case": "formal use case",
        "formal_check_report": "formal check report",
        "requirement_packet": "requirement packet",
        "trace_graph": "trace graph",
        "designer_analysis": "designer analysis",
        "requirement_graph": "requirement graph",
        "class_diagram_graph": "class diagram graph",
        "sequence_diagram_graph": "sequence diagram graph",
        "design_artifact": "design artifact",
        "requirement_design_trace": "requirement-design trace",
        "missing_design_links": "missing design links",
        "design_validation_report": "design validation report",
        "design_quality_report": "design quality report",
        "implementation_plan": "implementation plan",
        "implementation_plan_structured": "implementation plan structured data",
        "implementation_plan_review": "implementation plan review",
        "effective_design_contract": "effective design contract",
        "developer_work_packet": "developer work packet",
        "run_workspace": "run workspace",
        "code_change_report": "code change report",
        "test_report": "verification test report",
        "requirement_design_code_trace": "requirement-design-code trace",
        "implementation_repair_feedback": "implementation repair feedback",
        "qa_generated_tests": "QA generated tests",
        "qa_test_execution": "QA test execution",
        "qa_test_report": "QA test report",
        "qa_repair_feedback": "QA repair feedback",
        "github_pr_body": "GitHub PR body",
        "github_code_review": "GitHub code review",
        "github_code_review_approval": "GitHub code review approval",
        "tool_sync_report": "tool sync report",
    }
    priority = (
        "code_change_report",
        "test_report",
        "requirement_design_code_trace",
        "qa_test_report",
        "github_code_review",
        "run_workspace",
        "implementation_plan",
        "design_artifact",
        "requirement_packet",
        "trace_graph",
    )
    visible_keys = [str(key) for key in value if not str(key).endswith("_raw_response")]
    keys = [key for key in priority if key in visible_keys]
    keys.extend(key for key in visible_keys if key not in keys)
    names = [labels.get(key, key) for key in keys]
    if len(names) > 6:
        return f"{', '.join(names[:6])}, +{len(names) - 6} artifact khác"
    return ", ".join(names)


def _format_raw_status(current_phase: str, status: str) -> str:
    parts = [part for part in (current_phase, status) if part]
    if len(parts) == 2 and parts[0] == parts[1]:
        return parts[0]
    return " / ".join(parts)


def _title_tool(tool: str) -> str:
    mapping = {"jira": "Jira", "confluence": "Confluence", "github": "GitHub"}
    return mapping.get(tool.lower(), _clip_text(tool, 40))


def _safe_http_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return text
    return ""


def _looks_like_local_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("/", "\\\\", "file:")))


def _inline_code(value: Any) -> str:
    return str(value or "").replace("`", "'").replace("\r", " ").replace("\n", " ")


def _clip_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    # SDLC summaries often wrap Windows paths in Markdown backticks. Redact the
    # whole quoted value before the generic token matcher, otherwise a path that
    # contains spaces is only hidden up to its first space.
    cleaned = re.sub(r"(?i)`[A-Z]:[\\/][^`\r\n]+`", "`[local path]`", cleaned)
    cleaned = re.sub(r'(?i)"[A-Z]:[\\/][^"\r\n]+"', '"[local path]"', cleaned)
    cleaned = re.sub(r"(?i)'[A-Z]:[\\/][^'\r\n]+'", "'[local path]'", cleaned)
    cleaned = re.sub(r"(?i)\b[A-Z]:[\\/][^\s,;)\]]+", "[local path]", cleaned)
    cleaned = re.sub(r"(?i)file://[^\s,;)\]]+", "[local path]", cleaned)
    cleaned = re.sub(
        r"(?i)(?<!:)\b/(?:Users|home|tmp|var|opt|workspace)/[^\s,;)\]]+",
        "[local path]",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\b(token|api[_-]?key|secret)\s*=\s*[^\s,;]+", r"\1=[redacted]", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"
