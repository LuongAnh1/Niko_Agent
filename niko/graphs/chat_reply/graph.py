"""Graph nghiệp vụ chính cho một lượt chat.

Gateway chỉ giao message và callback gửi reply; file này quyết định luồng:
local rule, decision/Fast triage, Deep background job, followup khi Deep bận,
ghi trace và ghi memory baseline. Đây chưa phải graph framework tổng quát,
mà là orchestration cụ thể cho Niko Telegram baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Callable

from niko.harness.runtime_log import default_runtime_logger
from niko.harness.trace import TraceLogger, TraceTurn, default_trace_logger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore, default_memory_store
import niko.graphs.chat_reply.prompts as prompts
from niko.graphs.chat_reply.router import (
    AgentRoute,
    ROUTE_BUSY_REPLY,
    ROUTE_DELAYED_DEEP_AGENT,
    ROUTE_FAST_AGENT,
    ROUTE_LOCAL_REPLY,
    decide_agent_route,
)
from niko.config import env_value
import niko.runtime as runtime


AGENT_MODE_SINGLE = "single"
AGENT_MODE_TWO_AGENT = "two_agent"

ReplyCallback = Callable[[str], None]
NotifyCallback = Callable[[], None]


@dataclass
class DeepAgentJob:
    """Một Deep job đang chạy nền trong phạm vi một conversation."""

    conversation_id: str
    user_key: str
    prompt: str
    started_at: float
    trace_id: str = ""
    followups: list[str] = field(default_factory=list)
    deep_context: str = ""


def two_agent_mode_enabled() -> bool:
    """`two_agent` bật router local/Fast/Deep; `single` đẩy thẳng Deep."""
    mode = env_value("NIKO_AGENT_MODE", AGENT_MODE_SINGLE, legacy_name="TELEGRAM_AGENT_MODE").strip().lower()
    return mode in {AGENT_MODE_TWO_AGENT, "dual", "2", "true", "on"}


def format_probability_map(probabilities: dict[str, float]) -> str:
    """Rút gọn xác suất để runtime log đọc được trong một dòng."""
    items = [f"{key}={value:.3f}" for key, value in sorted(probabilities.items())]
    return "{" + ", ".join(items) + "}"


def format_fast_triage_log(decision: prompts.FastAgentDecision) -> str:
    """Dòng log cho biết triage dùng provider nào và model đã quyết định gì."""
    provider = decision.provider or "legacy_fast_agent"
    parts = [
        f"provider={provider}",
        f"route={decision.route}",
        f"has_reply={'yes' if decision.reply else 'no'}",
    ]
    if decision.model:
        parts.append(f"model={decision.model}")
    if decision.label:
        parts.append(f"label={decision.label}")
    if decision.confidence is not None:
        parts.append(f"confidence={decision.confidence:.3f}")
    if decision.probabilities:
        parts.append(f"probabilities={format_probability_map(decision.probabilities)}")
    return "Fast triage: " + " ".join(parts)


def runtime_log(event: str, message: str, *, level: str = "info", data: dict | None = None) -> None:
    default_runtime_logger().event("chat_graph", event, message, level=level, data=data or {})


class ChatReplyGraph:
    """Điều phối một turn chat và giữ trạng thái Deep job đang chạy."""

    def __init__(
        self,
        memory_store: MemoryStore | None = None,
        memory_runtime: MemoryRuntime | None = None,
        trace_logger: TraceLogger | None = None,
    ) -> None:
        self.deep_jobs: dict[str, DeepAgentJob] = {}
        self.deep_jobs_lock = threading.Lock()
        self.deep_agent_lock = threading.Lock()
        self._memory_store = memory_store
        self._memory_runtime = memory_runtime
        self._trace_logger = trace_logger

    @property
    def memory_store(self) -> MemoryStore:
        if self._memory_store is None:
            self._memory_store = default_memory_store()
        return self._memory_store

    @property
    def memory_runtime(self) -> MemoryRuntime:
        if self._memory_runtime is None:
            self._memory_runtime = MemoryRuntime(store=self.memory_store)
        return self._memory_runtime

    @property
    def trace_logger(self) -> TraceLogger:
        if self._trace_logger is None:
            self._trace_logger = default_trace_logger()
        return self._trace_logger

    def conversation_id_for(self, gateway_message) -> str:
        """Conversation ưu tiên chat_id; thiếu chat_id thì fallback user key."""
        return str(gateway_message.chat_id or gateway_message.user.key)

    def start_chat_turn(self, conversation_id: str, prompt: str, gateway_message) -> TraceTurn:
        """Mở trace và ghi incoming chat log cho một turn đã chuẩn hóa."""
        trace_turn = self._start_trace_turn(conversation_id, prompt, gateway_message)
        self._record_chat(conversation_id, "user", prompt, gateway_message, route="incoming", trace_id=trace_turn.turn_id)
        return trace_turn

    def decide_two_agent_route(self, prompt: str, conversation_id: str, trace_id: str) -> AgentRoute:
        """Tính route chat hai-agent và ghi trace/runtime log như luồng cũ."""
        route = decide_agent_route(
            prompt,
            deep_job_active=self.has_active_deep_job(conversation_id),
            fast_agent_available=prompts.fast_triage_available(),
        )
        runtime_log(
            "route_decision",
            f"Agent route: {route.kind} ({route.reason})",
            data={"route": route.kind, "reason": route.reason, "trace_id": trace_id},
        )
        self.trace_logger.event(
            trace_id,
            "route_decision",
            {"route": route.kind, "reason": route.reason, "mode": "two_agent"},
        )
        return route

    def handle_busy_reply(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        trace_turn: TraceTurn,
        route: AgentRoute,
    ) -> str:
        """Append followup vào Deep job active và trả busy reply."""
        active_job = self.add_deep_job_followup(conversation_id, prompt)
        final_reply = prompts.ensure_reply_suffix(prompts.build_deep_busy_reply(active_job))
        self._record_chat(
            conversation_id,
            "assistant",
            final_reply,
            gateway_message,
            route=route.kind,
            trace_id=trace_turn.turn_id,
        )
        self.trace_logger.turn_end(trace_turn.turn_id, reply=final_reply, status="ok", data={"route": route.kind})
        self._deliver_reply_safely(deliver_reply, final_reply, trace_turn.turn_id)
        return route.kind

    def finish_workflow_reply(
        self,
        conversation_id: str,
        reply: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        *,
        route: str,
        trace_id: str,
        meta: dict | None = None,
    ) -> str:
        """Ghi và gửi reply của workflow cấp turn bên ngoài chat graph."""
        final_reply = prompts.ensure_reply_suffix(reply)
        self._record_chat(
            conversation_id,
            "assistant",
            final_reply,
            gateway_message,
            route=route,
            trace_id=trace_id,
            meta=meta,
        )
        self.trace_logger.turn_end(trace_id, reply=final_reply, status="ok", data={"route": route})
        self._deliver_reply_safely(deliver_reply, final_reply, trace_id)
        return route

    def get_active_deep_job(self, conversation_id: str) -> DeepAgentJob | None:
        with self.deep_jobs_lock:
            return self.deep_jobs.get(conversation_id)

    def has_active_deep_job(self, conversation_id: str) -> bool:
        return self.get_active_deep_job(conversation_id) is not None

    def add_deep_job_followup(self, conversation_id: str, prompt: str) -> DeepAgentJob | None:
        """Khi Deep đang bận, message mới được ghi như followup của job hiện tại."""
        with self.deep_jobs_lock:
            job = self.deep_jobs.get(conversation_id)
            if job and prompt.strip():
                job.followups.append(prompt.strip())
                self.trace_logger.event(
                    job.trace_id,
                    "deep_job_followup_added",
                    {
                        "conversation_id": conversation_id,
                        "followup_count": len(job.followups),
                        "prompt": prompts.truncate_text(prompt, 1200),
                    },
                )
            return job

    def start_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
        trace_id: str = "",
        deep_context: str = "",
    ) -> bool:
        """Public helper để queue Deep job từ ngoài graph nếu cần."""
        if not self._reserve_deep_agent_job(conversation_id, prompt, gateway_message, trace_id, deep_context):
            return False

        self._start_deep_agent_thread(conversation_id, prompt, gateway_message, deliver_reply, notify_working)
        return True

    def _reserve_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        trace_id: str,
        deep_context: str = "",
    ) -> bool:
        """Đặt cờ job trước khi start thread để followup không chạy đua."""
        with self.deep_jobs_lock:
            if conversation_id in self.deep_jobs:
                return False
            self.deep_jobs[conversation_id] = DeepAgentJob(
                conversation_id,
                gateway_message.user.key,
                prompt,
                time.time(),
                trace_id=trace_id,
                deep_context=deep_context,
            )

        self.trace_logger.event(
            trace_id,
            "deep_job_queued",
            {
                "conversation_id": conversation_id,
                "user_key": gateway_message.user.key,
                "extra_context_chars": len(deep_context or ""),
            },
        )
        return True

    def _start_deep_agent_thread(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> None:
        """Start Deep ở daemon thread để Telegram nhận wait reply ngay."""
        active_job = self.get_active_deep_job(conversation_id)
        trace_id = active_job.trace_id if active_job else ""
        self.trace_logger.event(
            trace_id,
            "deep_job_started",
            {"conversation_id": conversation_id, "user_key": gateway_message.user.key},
        )
        thread = threading.Thread(
            target=self.run_deep_agent_job,
            args=(conversation_id, prompt, gateway_message, deliver_reply, notify_working),
            daemon=True,
        )
        thread.start()

    def run_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> None:
        """Vòng đời Deep job: gọi runtime, compose final, ghi memory/trace, cleanup."""
        active_job = self.get_active_deep_job(conversation_id)
        trace_id = active_job.trace_id if active_job else ""
        try:
            with self.deep_agent_lock:
                # Một Deep call tại một thời điểm để tránh nhiều CLI nặng cùng tranh máy.
                self._notify_working_async(notify_working, trace_id)
                self.trace_logger.event(trace_id, "deep_agent_call_started", {"conversation_id": conversation_id})
                deep_answer = runtime.call_deep_agent(
                    prompt,
                    gateway_message,
                    trace_id=trace_id,
                    trace_logger=self.trace_logger,
                    extra_context=active_job.deep_context if active_job else "",
                )
                self.trace_logger.event(
                    trace_id,
                    "deep_agent_call_finished",
                    {"answer_length": len(deep_answer or "")},
                )

            active_job = self.get_active_deep_job(conversation_id)
            answer = prompts.compose_deep_answer_for_user(prompt, gateway_message, deep_answer, active_job)
            final_reply = prompts.ensure_reply_suffix(answer)
            self._record_chat(
                conversation_id,
                "assistant",
                final_reply,
                gateway_message,
                route="deep_agent_final",
                trace_id=trace_id,
            )
            self._record_deep_episode(conversation_id, prompt, final_reply, gateway_message, active_job, trace_id)
            self.trace_logger.turn_end(
                trace_id,
                reply=final_reply,
                status="ok",
                data={"route": "deep_agent_final"},
            )
            self._deliver_reply_safely(deliver_reply, final_reply, trace_id)
        except Exception as exc:
            active_job = self.get_active_deep_job(conversation_id)
            error_text = f"Loi deep agent: {exc}"
            self.trace_logger.event(trace_id, "deep_agent_error", {"error": str(exc)})
            # Lỗi Deep vẫn cố đi qua Fast error task để user nhận câu gọn, không raw stack.
            answer = prompts.try_call_fast_agent(
                prompt,
                gateway_message,
                task=prompts.FAST_AGENT_TASK_ERROR,
                deep_answer=error_text,
                active_job=active_job,
            )
            final_reply = prompts.ensure_reply_suffix(answer or error_text)
            self._record_chat(
                conversation_id,
                "assistant",
                final_reply,
                gateway_message,
                route="deep_agent_error",
                trace_id=trace_id,
                meta={"error": str(exc)},
            )
            self.trace_logger.turn_end(
                trace_id,
                reply=final_reply,
                status="error",
                data={"error": str(exc)},
            )
            self._deliver_reply_safely(deliver_reply, final_reply, trace_id)
        finally:
            # Dù thành công hay lỗi, conversation phải được mở khóa cho turn sau.
            with self.deep_jobs_lock:
                self.deep_jobs.pop(conversation_id, None)

    def handoff_to_deep_agent(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
        wait_reply: str | None = None,
        trace_id: str = "",
        deep_context: str = "",
        wait_route: str = "deep_agent_wait",
    ) -> None:
        """Gửi wait reply trước, rồi mới start Deep để UX Telegram không bị im lặng."""
        started = self._reserve_deep_agent_job(conversation_id, prompt, gateway_message, trace_id, deep_context)
        if started:
            final_reply = prompts.ensure_reply_suffix(wait_reply or prompts.build_deep_wait_reply())
            self._record_chat(
                conversation_id,
                "assistant",
                final_reply,
                gateway_message,
                route=wait_route,
                trace_id=trace_id,
            )
            self.trace_logger.event(trace_id, "wait_reply_delivered", {"reply": prompts.truncate_text(final_reply, 1200)})
            self._deliver_reply_safely(deliver_reply, final_reply, trace_id)
            self._start_deep_agent_thread(conversation_id, prompt, gateway_message, deliver_reply, notify_working)
        else:
            # Nếu có race hoặc job đã tồn tại, message này thành followup thay vì tạo job thứ hai.
            active_job = self.add_deep_job_followup(conversation_id, prompt)
            final_reply = prompts.ensure_reply_suffix(prompts.build_deep_busy_reply(active_job))
            self._record_chat(
                conversation_id,
                "assistant",
                final_reply,
                gateway_message,
                route="busy_reply",
                trace_id=trace_id,
            )
            self.trace_logger.turn_end(trace_id, reply=final_reply, status="ok", data={"route": "busy_reply"})
            self._deliver_reply_safely(deliver_reply, final_reply, trace_id)

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        """Entrypoint tương thích cho chat graph khi không đi qua `NikoApp`."""
        conversation_id = self.conversation_id_for(gateway_message)
        trace_turn = self.start_chat_turn(conversation_id, prompt, gateway_message)

        if two_agent_mode_enabled():
            route = self.decide_two_agent_route(prompt, conversation_id, trace_turn.turn_id)
            return self.handle_two_agent_message(
                prompt,
                gateway_message,
                deliver_reply,
                notify_working,
                trace_turn=trace_turn,
                route=route,
            )

        return self.handle_single_agent_message(prompt, gateway_message, deliver_reply, notify_working, trace_turn)

    def handle_single_agent_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None,
        trace_turn: TraceTurn,
    ) -> str:
        """Single mode: gọi Deep đồng bộ, không fast triage hay background handoff."""
        conversation_id = self.conversation_id_for(gateway_message)
        try:
            self._notify_working_async(notify_working, trace_turn.turn_id)
            self.trace_logger.event(trace_turn.turn_id, "route_decision", {"route": "deep_agent", "mode": "single"})
            answer = prompts.ensure_reply_suffix(
                runtime.call_deep_agent(
                    prompt,
                    gateway_message,
                    trace_id=trace_turn.turn_id,
                    trace_logger=self.trace_logger,
                )
            )
            self._record_chat(
                conversation_id,
                "assistant",
                answer,
                gateway_message,
                route="deep_agent",
                trace_id=trace_turn.turn_id,
            )
            self._record_deep_episode(conversation_id, prompt, answer, gateway_message, None, trace_turn.turn_id)
            self.trace_logger.turn_end(trace_turn.turn_id, reply=answer, status="ok", data={"route": "deep_agent"})
            self._deliver_reply_safely(deliver_reply, answer, trace_turn.turn_id)
            return "deep_agent"
        except Exception as exc:
            self.trace_logger.turn_end(trace_turn.turn_id, status="error", data={"error": str(exc)})
            raise

    def handle_two_agent_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
        trace_turn: TraceTurn | None = None,
        route: AgentRoute | None = None,
    ) -> str:
        """Luồng chat two-agent thuần: local/Fast/Deep/background."""
        conversation_id = self.conversation_id_for(gateway_message)
        if trace_turn is None:
            trace_turn = self.start_chat_turn(conversation_id, prompt, gateway_message)

        if route is None:
            route = self.decide_two_agent_route(prompt, conversation_id, trace_turn.turn_id)

        if route.kind == ROUTE_BUSY_REPLY:
            return self.handle_busy_reply(conversation_id, prompt, gateway_message, deliver_reply, trace_turn, route)

        if route.kind == ROUTE_LOCAL_REPLY:
            # Local reply là nhánh rẻ nhất: không gọi Nimble/Fable/Deep.
            final_reply = prompts.ensure_reply_suffix(route.reply)
            self._record_chat(
                conversation_id,
                "assistant",
                final_reply,
                gateway_message,
                route=route.kind,
                trace_id=trace_turn.turn_id,
            )
            self.trace_logger.turn_end(trace_turn.turn_id, reply=final_reply, status="ok", data={"route": route.kind})
            self._deliver_reply_safely(deliver_reply, final_reply, trace_turn.turn_id)
            return route.kind

        if route.kind == ROUTE_FAST_AGENT:
            self.trace_logger.event(trace_turn.turn_id, "fast_triage_started", {})
            self._notify_working_async(notify_working, trace_turn.turn_id)
            decision = prompts.try_call_fast_agent_decision(prompt, gateway_message)
            if decision:
                decision_meta = {
                    "decision": decision.route,
                    "has_reply": bool(decision.reply),
                }
                if decision.provider:
                    decision_meta["provider"] = decision.provider
                if decision.confidence is not None:
                    decision_meta["confidence"] = decision.confidence
                if decision.label:
                    decision_meta["decision_label"] = decision.label
                if decision.probabilities:
                    decision_meta["probabilities"] = decision.probabilities
                if decision.model:
                    decision_meta["model"] = decision.model
                if decision.usage:
                    decision_meta["usage"] = decision.usage
                self.trace_logger.event(
                    trace_turn.turn_id,
                    "fast_triage_finished",
                    decision_meta,
                )
                runtime_log(
                    "fast_triage_finished",
                    format_fast_triage_log(decision),
                    data={**decision_meta, "trace_id": trace_turn.turn_id},
                )
            if decision and decision.route == prompts.FAST_DECISION_REPLY_NOW:
                # Nimble chỉ quyết định route; nếu không có text thì Fable sinh reply nhanh.
                fast_reply = decision.reply or prompts.try_call_fast_agent(
                    prompt,
                    gateway_message,
                    task=prompts.FAST_AGENT_TASK_REPLY,
                )
                if not fast_reply:
                    runtime_log(
                        "fast_reply_fallback_to_deep",
                        "Fast triage chon reply_now nhung khong tao duoc reply, chuyen sang deep agent.",
                        level="warning",
                        data={"trace_id": trace_turn.turn_id},
                    )
                    self.trace_logger.event(trace_turn.turn_id, "fast_reply_fallback_to_deep", {})
                else:
                    final_reply = prompts.ensure_reply_suffix(fast_reply)
                    self._record_chat(
                        conversation_id,
                        "assistant",
                        final_reply,
                        gateway_message,
                        route=route.kind,
                        trace_id=trace_turn.turn_id,
                        meta={
                            "fast_decision": decision.route,
                            "decision_provider": decision.provider,
                        },
                    )
                    self.trace_logger.turn_end(trace_turn.turn_id, reply=final_reply, status="ok", data={"route": route.kind})
                    self._deliver_reply_safely(deliver_reply, final_reply, trace_turn.turn_id)
                    return route.kind
            if decision and decision.route == prompts.FAST_DECISION_SEND_TO_DEEP:
                self.handoff_to_deep_agent(
                    conversation_id,
                    prompt,
                    gateway_message,
                    deliver_reply,
                    notify_working,
                    wait_reply=decision.reply,
                    trace_id=trace_turn.turn_id,
                )
                return route.kind
            runtime_log(
                "fast_triage_fallback_to_deep",
                "Fast agent khong co cau tra loi truc tiep hop le, chuyen sang deep agent.",
                level="warning",
                data={"trace_id": trace_turn.turn_id},
            )
            self.trace_logger.event(trace_turn.turn_id, "fast_triage_fallback_to_deep", {})

        # Deep/default path: các route chắc chắn sâu, hoặc triage lỗi/không trả reply hợp lệ.
        prompts.delay_before_deep_agent_if_needed(route.kind)
        self.handoff_to_deep_agent(
            conversation_id,
            prompt,
            gateway_message,
            deliver_reply,
            notify_working,
            trace_id=trace_turn.turn_id,
        )
        return route.kind

    def _start_trace_turn(self, conversation_id: str, prompt: str, gateway_message) -> TraceTurn:
        return self.trace_logger.turn_start(conversation_id, gateway_message.user.key, prompt, gateway_message)

    def _notify_working_async(self, notify_working: NotifyCallback | None, trace_id: str) -> None:
        """Typing indicator chạy nền để callback chậm không khóa route chính."""
        if notify_working is None:
            return

        thread = threading.Thread(
            target=self._safe_notify_working,
            args=(notify_working, trace_id),
            daemon=True,
        )
        thread.start()

    def _safe_notify_working(self, notify_working: NotifyCallback, trace_id: str) -> None:
        try:
            notify_working()
            self._trace_event_safely(trace_id, "notify_working_sent", {})
        except Exception as exc:
            self._trace_event_safely(trace_id, "notify_working_error", {"error": str(exc)})

    def _trace_event_safely(self, trace_id: str, kind: str, data: dict | None = None) -> None:
        try:
            self.trace_logger.event(trace_id, kind, data or {})
        except Exception:
            pass

    def _deliver_reply_safely(self, deliver_reply: ReplyCallback, reply: str, trace_id: str) -> None:
        """Reply delivery lỗi chỉ ghi trace; graph không retry Telegram ở đây."""
        try:
            deliver_reply(reply)
        except Exception as exc:
            self.trace_logger.event(trace_id, "reply_delivery_error", {"error": str(exc)})

    def _record_chat(
        self,
        conversation_id: str,
        role: str,
        content: str,
        gateway_message,
        route: str,
        trace_id: str,
        meta: dict | None = None,
    ) -> None:
        """Ghi chat_log vận hành; đây chưa phải semantic/episodic memory."""
        self.memory_runtime.record_chat_log(
            conversation_id,
            role,
            content,
            gateway_message,
            route,
            trace_id,
            self.trace_logger,
            meta=meta,
        )

    def _record_deep_episode(
        self,
        conversation_id: str,
        prompt: str,
        answer: str,
        gateway_message,
        active_job: DeepAgentJob | None,
        trace_id: str,
    ) -> None:
        """Ghi episode baseline sau khi Deep hoàn tất, kèm followup nếu có."""
        followups = active_job.followups if active_job else []
        self.memory_runtime.record_deep_episode(
            conversation_id,
            prompt,
            answer,
            gateway_message,
            followups,
            trace_id,
            self.trace_logger,
            route="deep_agent",
        )


__all__ = [
    "AGENT_MODE_SINGLE",
    "AGENT_MODE_TWO_AGENT",
    "ChatReplyGraph",
    "DeepAgentJob",
    "NotifyCallback",
    "ReplyCallback",
    "two_agent_mode_enabled",
]
