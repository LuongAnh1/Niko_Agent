"""Assembly root và điểm chọn workflow cấp turn hiện tại của Niko.

Theo tinh thần Waku, app là nơi ráp config/dependency và là entrypoint cấp turn
cho gateway. Phase 4/5 đã đưa workflow selection thật ra khỏi `ChatReplyGraph`:
memory correction và Jira issue flow được chọn ở đây, còn chat graph giữ phần
local/Fast/Deep.
"""

from __future__ import annotations

from collections.abc import Callable

from niko.graphs.chat_reply import ROUTE_BUSY_REPLY, ChatReplyGraph, two_agent_mode_enabled
from niko.graphs.jira_issue import JiraIssueAnalysisWorkflow, jira_tools_enabled
from niko.harness.runtime_log import default_runtime_logger
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore
from niko.sdlc.client import (
    ROUTE_SDLC_START,
    ROUTE_SDLC_STATUS,
    SdlcClient,
    SdlcResult,
    detect_sdlc_request,
    sdlc_enabled,
)


ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


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
    ) -> None:
        if chat_graph is not None and any(dep is not None for dep in (memory_store, memory_runtime, trace_logger)):
            raise ValueError("Chi truyen chat_graph hoac cac dependency de tao graph, khong truyen ca hai.")

        self.chat_graph = chat_graph or ChatReplyGraph(
            memory_store=memory_store,
            memory_runtime=memory_runtime,
            trace_logger=trace_logger,
        )

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

        if request.action == ROUTE_SDLC_STATUS:
            result = SdlcClient().get_status(run_id=request.run_id)
        else:
            result = SdlcClient().start_or_get_run(
                dataset=request.dataset,
                use_case=request.use_case,
                requested_by=_user_key(gateway_message),
            )

        self._log_workflow_selected(
            "sdlc_harness",
            result.action,
            conversation_id,
            trace_id,
            {
                "ok": result.ok,
                "run_id": result.run_id,
                "current_phase": result.current_phase,
                "status": result.status,
                "error_code": result.error_code,
            },
        )
        try:
            self.chat_graph.trace_logger.event(
                trace_id,
                "sdlc_harness_finished",
                {
                    "route": result.action,
                    "ok": result.ok,
                    "run_id": result.run_id,
                    "source_ref": result.source_ref,
                    "current_phase": result.current_phase,
                    "status": result.status,
                    "pending_checkpoint": result.pending_checkpoint,
                    "allowed_next_actions": result.allowed_next_actions,
                    "latest_artifacts": result.latest_artifacts,
                    "last_sync": result.last_sync,
                    "error_code": result.error_code,
                },
            )
        except Exception:
            pass

        return self.chat_graph.finish_workflow_reply(
            conversation_id,
            _format_sdlc_reply(result),
            gateway_message,
            deliver_reply,
            route=result.action,
            trace_id=trace_id,
            meta={"run_id": result.run_id, "ok": result.ok, "error_code": result.error_code},
        )

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


def _format_sdlc_reply(result: SdlcResult) -> str:
    """Đổi JSON trạng thái SDLC thành reply ngắn để người dùng quyết định bước tiếp."""
    if not result.ok:
        detail = result.message or "SDLC_Harness chưa xử lý được yêu cầu này."
        if result.error_code:
            return f"Dạ SDLC_Harness đang lỗi `{result.error_code}`: {detail}"
        return f"Dạ SDLC_Harness đang lỗi: {detail}"

    lines = ["Dạ em đã lấy trạng thái từ SDLC_Harness."]
    if result.run_id:
        lines.append(f"Run: `{result.run_id}`")
    if result.source_ref:
        lines.append(f"Nguồn: {result.source_ref}")
    if result.current_phase or result.status:
        status = " / ".join(part for part in (result.current_phase, result.status) if part)
        lines.append(f"Trạng thái: {status}")
    if result.summary_for_user:
        lines.append(result.summary_for_user)

    checkpoint_title = str(result.pending_checkpoint.get("title") or result.pending_checkpoint.get("name") or "").strip()
    if checkpoint_title:
        lines.append(f"Checkpoint cần duyệt: {checkpoint_title}")

    artifacts = _format_mapping_keys(result.latest_artifacts)
    if artifacts:
        lines.append(f"Artifact mới nhất: {artifacts}")

    actions = _format_actions(result.allowed_next_actions)
    if actions:
        lines.append(f"Hành động tiếp theo: {actions}")
    return "\n".join(lines)


def _format_mapping_keys(value: dict) -> str:
    keys = [str(key) for key in value if str(key).strip()]
    return ", ".join(keys[:5])


def _format_actions(actions: list[str]) -> str:
    return ", ".join(action for action in actions if action)


def create_niko_app(
    chat_graph=None,
    memory_store: MemoryStore | None = None,
    memory_runtime: MemoryRuntime | None = None,
    trace_logger: TraceLogger | None = None,
) -> NikoApp:
    """Tạo app mặc định; tham số test-only giúp giữ đường inject graph hiện có."""
    return NikoApp(
        chat_graph=chat_graph,
        memory_store=memory_store,
        memory_runtime=memory_runtime,
        trace_logger=trace_logger,
    )
