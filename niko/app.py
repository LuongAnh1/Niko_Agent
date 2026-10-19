"""Assembly root và điểm chọn workflow cấp turn hiện tại của Niko.

Theo tinh thần Waku, app là nơi ráp config/dependency và là entrypoint cấp turn
cho gateway. Workflow selection thật nằm ở đây: memory correction, Jira issue
flow và SDLC client được chọn trước khi chat graph xử lý local/Fast/Deep.
Pipeline requirement/artifact/sync của SDLC vẫn thuộc repo SDLC_Harness.
"""

from __future__ import annotations

from collections.abc import Callable
import time

from niko.graphs.chat_reply import ROUTE_BUSY_REPLY, ChatReplyGraph, two_agent_mode_enabled
from niko.graphs.jira_issue import JiraIssueAnalysisWorkflow, jira_tools_enabled
from niko.harness.runtime_log import default_runtime_logger
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore
from niko.sdlc.client import (
    ROUTE_SDLC_ANSWER_CLARIFICATION,
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_REVALIDATE,
    ROUTE_SDLC_REVALIDATE_DESIGN,
    ROUTE_SDLC_REVALIDATE_PLAN,
    ROUTE_SDLC_START,
    ROUTE_SDLC_START_JIRA,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcClient,
    SdlcRequest,
    SdlcResult,
    detect_sdlc_request,
    sdlc_enabled,
)
from niko.sdlc.control import code_review_approval_enabled, evaluate_sdlc_preflight, preflight_notice
from niko.sdlc.formatting import format_sdlc_reply
from niko.sdlc.jobs import SdlcJob, SdlcJobRunner


ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]
ROUTE_SDLC_JOB_QUEUED = "sdlc_job_queued"
ROUTE_SDLC_JOB_DUPLICATE = "sdlc_job_duplicate"
ROUTE_SDLC_JOB_FINAL = "sdlc_job_final"


def runtime_log(event: str, message: str, *, level: str = "info", data: dict | None = None) -> None:
    """Ghi log cấp app để dashboard thấy workflow nào đã nhận turn."""
    default_runtime_logger().event("niko_app", event, message, level=level, data=data or {})


class NikoApp:
    """Assembly root hiện tại, chọn workflow cấp turn trước khi vào chat graph."""

    def __init__(
        self,
        chat_graph=None,
        memory_store: MemoryStore | None = None,
        memory_runtime: MemoryRuntime | None = None,
        trace_logger: TraceLogger | None = None,
        sdlc_client: SdlcClient | None = None,
        sdlc_job_runner: SdlcJobRunner | None = None,
    ) -> None:
        if chat_graph is not None and any(dep is not None for dep in (memory_store, memory_runtime, trace_logger)):
            raise ValueError("Chi truyen chat_graph hoac cac dependency de tao graph, khong truyen ca hai.")

        self.chat_graph = chat_graph or ChatReplyGraph(
            memory_store=memory_store,
            memory_runtime=memory_runtime,
            trace_logger=trace_logger,
        )
        self._sdlc_client = sdlc_client
        self._sdlc_job_runner = sdlc_job_runner

    @property
    def sdlc_client(self) -> SdlcClient:
        if self._sdlc_client is None:
            self._sdlc_client = SdlcClient()
        return self._sdlc_client

    @property
    def sdlc_job_runner(self) -> SdlcJobRunner:
        if self._sdlc_job_runner is None:
            self._sdlc_job_runner = SdlcJobRunner(trace_logger=self.chat_graph.trace_logger)
        return self._sdlc_job_runner

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        """Xử lý một turn đã chuẩn hóa và trả route của workflow đã chạy."""
        if not self._owns_turn_selection():
            return self.chat_graph.handle_message(prompt, gateway_message, deliver_reply, notify_working)

        conversation_id = self.chat_graph.conversation_id_for(gateway_message)
        trace_turn = self.chat_graph.start_chat_turn(conversation_id, prompt, gateway_message)

        sdlc_route = self._handle_sdlc_prompt(
            conversation_id,
            prompt,
            gateway_message,
            deliver_reply,
            notify_working,
            trace_turn.turn_id,
        )
        if sdlc_route:
            return sdlc_route

        if not two_agent_mode_enabled():
            self._log_workflow_selected(
                "single_agent",
                "deep_agent",
                conversation_id,
                trace_turn.turn_id,
            )
            return self.chat_graph.handle_single_agent_message(
                prompt,
                gateway_message,
                deliver_reply,
                notify_working,
                trace_turn,
            )

        route = self.chat_graph.decide_two_agent_route(prompt, conversation_id, trace_turn.turn_id)
        if route.kind == ROUTE_BUSY_REPLY:
            self._log_workflow_selected(
                "busy_reply",
                route.kind,
                conversation_id,
                trace_turn.turn_id,
                {"reason": route.reason},
            )
            return self.chat_graph.handle_busy_reply(
                conversation_id,
                prompt,
                gateway_message,
                deliver_reply,
                trace_turn,
                route,
            )

        memory_route = self._handle_memory_correction(conversation_id, prompt, gateway_message, deliver_reply, trace_turn)
        if memory_route:
            return memory_route

        jira_route = self._handle_jira_issue_prompt(
            conversation_id,
            prompt,
            gateway_message,
            deliver_reply,
            notify_working,
            trace_turn.turn_id,
        )
        if jira_route:
            return jira_route

        self._log_workflow_selected(
            "normal_chat",
            route.kind,
            conversation_id,
            trace_turn.turn_id,
            {"reason": route.reason},
        )
        return self.chat_graph.handle_two_agent_message(
            prompt,
            gateway_message,
            deliver_reply,
            notify_working,
            trace_turn=trace_turn,
            route=route,
        )

    def _owns_turn_selection(self) -> bool:
        """Fake graph tối giản trong tests cũ vẫn có thể được delegate trực tiếp."""
        required = (
            "start_chat_turn",
            "decide_two_agent_route",
            "handle_single_agent_message",
            "handle_busy_reply",
            "finish_workflow_reply",
            "finish_background_workflow_reply",
        )
        return all(hasattr(self.chat_graph, name) for name in required)

    def _handle_memory_correction(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        trace_turn,
    ) -> str:
        correction = self.chat_graph.memory_runtime.handle_memory_correction(
            conversation_id,
            prompt,
            gateway_message,
            trace_turn.turn_id,
            self.chat_graph.trace_logger,
        )
        if not correction.handled:
            return ""
        self._log_workflow_selected(
            "memory_correction",
            correction.route,
            conversation_id,
            trace_turn.turn_id,
        )
        return self.chat_graph.finish_workflow_reply(
            conversation_id,
            correction.reply,
            gateway_message,
            deliver_reply,
            route=correction.route,
            trace_id=trace_turn.turn_id,
        )

    def _handle_sdlc_prompt(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None,
        trace_id: str,
    ) -> str:
        """Nếu bật SDLC_Harness, route use case/checkpoint qua repo workflow riêng."""
        if not sdlc_enabled():
            return ""

        request = detect_sdlc_request(prompt)
        if request is None:
            return ""

        if notify_working is not None:
            try:
                notify_working()
            except Exception:
                pass

        client = self.sdlc_client
        preflight = None
        if request.action == ROUTE_SDLC_STATUS:
            result = client.get_status(run_id=request.run_id)
        elif request.action in {
            ROUTE_SDLC_ANSWER_CLARIFICATION,
            ROUTE_SDLC_APPROVE,
            ROUTE_SDLC_CONTINUE,
            ROUTE_SDLC_REVALIDATE,
            ROUTE_SDLC_REVALIDATE_DESIGN,
            ROUTE_SDLC_REVALIDATE_PLAN,
            ROUTE_SDLC_SYNC,
        }:
            status_result = client.get_status(run_id=request.run_id)
            preflight = evaluate_sdlc_preflight(
                request.action,
                status_result,
                allow_code_review_approval=code_review_approval_enabled(),
                question_id=request.question_id,
            )
            if (
                request.action
                in {
                    ROUTE_SDLC_ANSWER_CLARIFICATION,
                    ROUTE_SDLC_APPROVE,
                    ROUTE_SDLC_CONTINUE,
                    ROUTE_SDLC_REVALIDATE,
                    ROUTE_SDLC_REVALIDATE_DESIGN,
                    ROUTE_SDLC_REVALIDATE_PLAN,
                    ROUTE_SDLC_SYNC,
                }
                and preflight.allowed
                and not preflight.execute_now
            ):
                return self._queue_sdlc_action(
                    conversation_id,
                    request,
                    gateway_message,
                    deliver_reply,
                    trace_id,
                    status_result,
                    preflight.reason,
                )
            if preflight.execute_now:
                result = client.approve_checkpoint(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            else:
                result = status_result
        elif request.action == ROUTE_SDLC_START_JIRA:
            result = client.start_from_jira(
                issue_key=request.issue_key,
                requested_by=_user_key(gateway_message),
            )
        else:
            result = client.start_or_get_run(
                dataset=request.dataset,
                use_case=request.use_case,
                requested_by=_user_key(gateway_message),
            )

        write_dispatched = request.action in {ROUTE_SDLC_START, ROUTE_SDLC_START_JIRA} or bool(
            preflight and preflight.execute_now
        )

        self._log_workflow_selected(
            "sdlc_harness",
            request.action,
            conversation_id,
            trace_id,
            {
                "ok": result.ok,
                "run_id": result.run_id,
                "current_phase": result.current_phase,
                "status": result.status,
                "error_code": result.error_code,
                "preflight_reason": preflight.reason if preflight else "",
                "write_dispatched": write_dispatched,
            },
        )
        try:
            self.chat_graph.trace_logger.event(
                trace_id,
                "sdlc_harness_finished",
                {
                    "route": request.action,
                    "ok": result.ok,
                    "run_id": result.run_id,
                    "source_ref": result.source_ref,
                    "current_phase": result.current_phase,
                    "status": result.status,
                    "pending_checkpoint": result.pending_checkpoint,
                    "allowed_next_actions": result.allowed_next_actions,
                    "latest_artifacts": result.latest_artifacts,
                    "last_sync": result.last_sync,
                    "last_error": result.last_error,
                    "error_code": result.error_code,
                    "preflight_reason": preflight.reason if preflight else "",
                    "write_dispatched": write_dispatched,
                },
            )
        except Exception:
            pass

        reply = format_sdlc_reply(result)
        notice = preflight_notice(preflight) if preflight else ""
        if notice:
            reply = f"{notice}\n\n{reply}"

        return self.chat_graph.finish_workflow_reply(
            conversation_id,
            reply,
            gateway_message,
            deliver_reply,
            route=request.action,
            trace_id=trace_id,
            meta={
                "run_id": result.run_id,
                "ok": result.ok,
                "error_code": result.error_code,
                "preflight_reason": preflight.reason if preflight else "",
                "write_dispatched": write_dispatched,
            },
        )

    def _queue_sdlc_action(
        self,
        conversation_id: str,
        request: SdlcRequest,
        gateway_message,
        deliver_reply: ReplyCallback,
        trace_id: str,
        status_result: SdlcResult,
        preflight_reason: str,
    ) -> str:
        action_label = {
            ROUTE_SDLC_ANSWER_CLARIFICATION: "trả lời câu hỏi làm rõ requirement",
            ROUTE_SDLC_CONTINUE: "chạy tiếp Phase 4",
            ROUTE_SDLC_REVALIDATE: "chạy lại Verification Runner và Validator",
            ROUTE_SDLC_REVALIDATE_DESIGN: "tạo version mới và chạy lại Design Validator",
            ROUTE_SDLC_REVALIDATE_PLAN: "tạo version mới và kiểm tra lại implementation plan",
            ROUTE_SDLC_SYNC: "đồng bộ lại",
            ROUTE_SDLC_APPROVE: "duyệt Code Review và chuyển Draft PR sang Ready for review",
        }[request.action]
        job = SdlcJob(
            run_id=request.run_id,
            action=request.action,
            conversation_id=conversation_id,
            user_key=_user_key(gateway_message),
            trace_id=trace_id,
            started_at=time.time(),
        )

        def task() -> SdlcResult:
            if request.action == ROUTE_SDLC_ANSWER_CLARIFICATION:
                return self.sdlc_client.answer_clarification(
                    run_id=request.run_id,
                    question_id=request.question_id,
                    answer=request.answer,
                    answered_by=_user_key(gateway_message),
                )
            if request.action == ROUTE_SDLC_CONTINUE:
                return self.sdlc_client.continue_run(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            if request.action == ROUTE_SDLC_REVALIDATE:
                return self.sdlc_client.revalidate_implementation(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            if request.action == ROUTE_SDLC_REVALIDATE_DESIGN:
                return self.sdlc_client.revalidate_design(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            if request.action == ROUTE_SDLC_REVALIDATE_PLAN:
                return self.sdlc_client.revalidate_implementation_plan(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            if request.action == ROUTE_SDLC_SYNC:
                return self.sdlc_client.sync_run(
                    run_id=request.run_id,
                    requested_by=_user_key(gateway_message),
                )
            return self.sdlc_client.approve_checkpoint(
                run_id=request.run_id,
                requested_by=_user_key(gateway_message),
            )

        def on_reserved() -> None:
            self._log_workflow_selected(
                "sdlc_harness",
                request.action,
                conversation_id,
                trace_id,
                {
                    "ok": status_result.ok,
                    "run_id": request.run_id,
                    "current_phase": status_result.current_phase,
                    "status": status_result.status,
                    "error_code": status_result.error_code,
                    "preflight_reason": preflight_reason,
                    "write_dispatched": True,
                    "question_id": request.question_id,
                },
            )
            queued_reply = (
                f"Dạ, em đã đưa yêu cầu {action_label} cho run `{request.run_id}` vào xử lý nền. "
                "Em sẽ gửi kết quả khi SDLC Harness hoàn tất. Anh vẫn có thể hỏi trạng thái run trong lúc chờ."
            )
            if request.action == ROUTE_SDLC_APPROVE:
                queued_reply += " Thao tác này không merge PR; anh vẫn review và merge trực tiếp trên GitHub."
            self.chat_graph.finish_workflow_reply(
                conversation_id,
                queued_reply,
                gateway_message,
                deliver_reply,
                route=ROUTE_SDLC_JOB_QUEUED,
                trace_id=trace_id,
                meta={
                    "action": request.action,
                    "run_id": request.run_id,
                    "preflight_reason": preflight_reason,
                    "write_dispatched": True,
                },
            )

        def on_complete(result: SdlcResult) -> None:
            self.chat_graph.finish_background_workflow_reply(
                conversation_id,
                format_sdlc_reply(result),
                gateway_message,
                deliver_reply,
                route=ROUTE_SDLC_JOB_FINAL,
                trace_id=trace_id,
                meta={
                    "action": request.action,
                    "run_id": result.run_id or request.run_id,
                    "ok": result.ok,
                    "status": result.status,
                    "error_code": result.error_code,
                    "question_id": request.question_id,
                },
            )

        started = self.sdlc_job_runner.submit(job, task, on_reserved, on_complete)
        if started:
            return request.action

        self._log_workflow_selected(
            "sdlc_harness",
            request.action,
            conversation_id,
            trace_id,
            {
                "run_id": request.run_id,
                "preflight_reason": "duplicate_background_job",
                "write_dispatched": False,
                "question_id": request.question_id,
            },
        )
        duplicate_reply = (
            f"Run `{request.run_id}` đang có tác vụ SDLC chạy nền nên em không tạo tiến trình thứ hai. "
            "Anh có thể hỏi trạng thái run để xem tiến độ hiện tại."
        )
        self.chat_graph.finish_workflow_reply(
            conversation_id,
            duplicate_reply,
            gateway_message,
            deliver_reply,
            route=ROUTE_SDLC_JOB_DUPLICATE,
            trace_id=trace_id,
            meta={
                "action": request.action,
                "run_id": request.run_id,
                "preflight_reason": "duplicate_background_job",
                "write_dispatched": False,
                "question_id": request.question_id,
            },
        )
        return request.action

    def _handle_jira_issue_prompt(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None,
        trace_id: str,
    ) -> str:
        """Nếu bật Jira tools, chạy Jira issue workflow trước normal chat."""
        if not jira_tools_enabled():
            return ""

        result = JiraIssueAnalysisWorkflow().handle(
            prompt=prompt,
            conversation_id=conversation_id,
            user_key=gateway_message.user.key,
            trace_id=trace_id,
            trace_logger=self.chat_graph.trace_logger,
            gateway_message=gateway_message,
            recent_turns=self._recent_chat_for_jira_gate(conversation_id),
        )
        if not result.handled:
            return ""

        self._log_workflow_selected(
            "jira_issue",
            result.route,
            conversation_id,
            trace_id,
            {
                "issue_key": result.issue_key,
                "issue_keys": result.issue_keys,
                "has_deep_context": bool(result.deep_context),
                "error": result.error,
            },
        )
        self.chat_graph.trace_logger.event(
            trace_id,
            "jira_issue_workflow_finished",
            {
                "route": result.route,
                "issue_key": result.issue_key,
                "issue_keys": result.issue_keys,
                "has_deep_context": bool(result.deep_context),
                "error": result.error,
                "gate": result.gate,
            },
        )
        if result.deep_context:
            self.chat_graph.handoff_to_deep_agent(
                conversation_id,
                prompt,
                gateway_message,
                deliver_reply,
                notify_working,
                wait_reply=result.wait_reply,
                trace_id=trace_id,
                deep_context=result.deep_context,
                wait_route="jira_issue_wait",
            )
            return result.route

        return self.chat_graph.finish_workflow_reply(
            conversation_id,
            result.reply,
            gateway_message,
            deliver_reply,
            route=result.route,
            trace_id=trace_id,
            meta={"issue_key": result.issue_key, "error": result.error},
        )

    def _recent_chat_for_jira_gate(self, conversation_id: str) -> list[dict]:
        """Lấy recent chat ngắn để Jira gate hiểu các câu như 'ticket vừa nãy'."""
        try:
            return self.chat_graph.memory_store.chat_history(conversation_id, limit=8)
        except Exception:
            return []

    def _log_workflow_selected(
        self,
        workflow: str,
        route: str,
        conversation_id: str,
        trace_id: str,
        data: dict | None = None,
    ) -> None:
        payload = {
            "workflow": workflow,
            "route": route,
            "conversation_id": conversation_id,
            "trace_id": trace_id,
        }
        if data:
            payload.update(data)
        runtime_log(
            "workflow_selected",
            f"NikoApp workflow: {workflow} route={route}",
            data=payload,
        )


def _user_key(gateway_message) -> str:
    """Lấy user key an toàn cho các workflow không phụ thuộc Telegram object thật."""
    user = getattr(gateway_message, "user", None)
    return str(getattr(user, "key", "") or "niko")


def create_niko_app(
    chat_graph=None,
    memory_store: MemoryStore | None = None,
    memory_runtime: MemoryRuntime | None = None,
    trace_logger: TraceLogger | None = None,
    sdlc_client: SdlcClient | None = None,
    sdlc_job_runner: SdlcJobRunner | None = None,
) -> NikoApp:
    """Tạo app mặc định; tham số test-only giúp giữ đường inject graph hiện có."""
    return NikoApp(
        chat_graph=chat_graph,
        memory_store=memory_store,
        memory_runtime=memory_runtime,
        trace_logger=trace_logger,
        sdlc_client=sdlc_client,
        sdlc_job_runner=sdlc_job_runner,
    )
