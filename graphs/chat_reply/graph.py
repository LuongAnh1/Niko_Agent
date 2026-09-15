from __future__ import annotations

from dataclasses import dataclass, field
import sys
import threading
import time
from typing import Callable

import graphs.chat_reply.prompts as prompts
from graphs.chat_reply.router import (
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
    conversation_id: str
    user_key: str
    prompt: str
    started_at: float
    followups: list[str] = field(default_factory=list)


def two_agent_mode_enabled() -> bool:
    mode = env_value("NIKO_AGENT_MODE", AGENT_MODE_SINGLE, legacy_name="TELEGRAM_AGENT_MODE").strip().lower()
    return mode in {AGENT_MODE_TWO_AGENT, "dual", "2", "true", "on"}


class ChatReplyGraph:
    def __init__(self) -> None:
        self.deep_jobs: dict[str, DeepAgentJob] = {}
        self.deep_jobs_lock = threading.Lock()
        self.deep_agent_lock = threading.Lock()

    def conversation_id_for(self, gateway_message) -> str:
        return str(gateway_message.chat_id or gateway_message.user.key)

    def get_active_deep_job(self, conversation_id: str) -> DeepAgentJob | None:
        with self.deep_jobs_lock:
            return self.deep_jobs.get(conversation_id)

    def has_active_deep_job(self, conversation_id: str) -> bool:
        return self.get_active_deep_job(conversation_id) is not None

    def add_deep_job_followup(self, conversation_id: str, prompt: str) -> DeepAgentJob | None:
        with self.deep_jobs_lock:
            job = self.deep_jobs.get(conversation_id)
            if job and prompt.strip():
                job.followups.append(prompt.strip())
            return job

    def start_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> bool:
        with self.deep_jobs_lock:
            if conversation_id in self.deep_jobs:
                return False
            self.deep_jobs[conversation_id] = DeepAgentJob(
                conversation_id,
                gateway_message.user.key,
                prompt,
                time.time(),
            )

        thread = threading.Thread(
            target=self.run_deep_agent_job,
            args=(conversation_id, prompt, gateway_message, deliver_reply, notify_working),
            daemon=True,
        )
        thread.start()
        return True

    def run_deep_agent_job(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> None:
        try:
            with self.deep_agent_lock:
                if notify_working:
                    notify_working()
                deep_answer = runtime.call_deep_agent(prompt, gateway_message)

            active_job = self.get_active_deep_job(conversation_id)
            answer = prompts.compose_deep_answer_for_user(prompt, gateway_message, deep_answer, active_job)
            deliver_reply(prompts.ensure_reply_suffix(answer))
        except Exception as exc:
            active_job = self.get_active_deep_job(conversation_id)
            error_text = f"Loi deep agent: {exc}"
            answer = prompts.try_call_fast_agent(
                prompt,
                gateway_message,
                task=prompts.FAST_AGENT_TASK_ERROR,
                deep_answer=error_text,
                active_job=active_job,
            )
            deliver_reply(prompts.ensure_reply_suffix(answer or error_text))
        finally:
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
    ) -> None:
        started = self.start_deep_agent_job(conversation_id, prompt, gateway_message, deliver_reply, notify_working)
        if started:
            answer = wait_reply or prompts.try_call_fast_agent(prompt, gateway_message, task=prompts.FAST_AGENT_TASK_WAIT)
            deliver_reply(prompts.ensure_reply_suffix(answer or prompts.build_deep_wait_reply()))
        else:
            active_job = self.add_deep_job_followup(conversation_id, prompt)
            answer = prompts.try_call_fast_agent(
                prompt,
                gateway_message,
                task=prompts.FAST_AGENT_TASK_BUSY,
                active_job=active_job,
            )
            deliver_reply(prompts.ensure_reply_suffix(answer or prompts.build_deep_busy_reply(active_job)))

    def handle_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        if two_agent_mode_enabled():
            return self.handle_two_agent_message(prompt, gateway_message, deliver_reply, notify_working)

        if notify_working:
            notify_working()
        answer = prompts.ensure_reply_suffix(runtime.call_deep_agent(prompt, gateway_message))
        deliver_reply(answer)
        return "deep_agent"

    def handle_two_agent_message(
        self,
        prompt: str,
        gateway_message,
        deliver_reply: ReplyCallback,
        notify_working: NotifyCallback | None = None,
    ) -> str:
        conversation_id = self.conversation_id_for(gateway_message)
        route = decide_agent_route(
            prompt,
            deep_job_active=self.has_active_deep_job(conversation_id),
            fast_agent_available=bool(prompts.fast_agent_command()),
        )
        print(f"Agent route: {route.kind} ({route.reason})")

        if route.kind == ROUTE_BUSY_REPLY:
            active_job = self.add_deep_job_followup(conversation_id, prompt)
            answer = prompts.try_call_fast_agent(
                prompt,
                gateway_message,
                task=prompts.FAST_AGENT_TASK_BUSY,
                active_job=active_job,
            )
            deliver_reply(prompts.ensure_reply_suffix(answer or prompts.build_deep_busy_reply(active_job)))
            return route.kind

        if route.kind == ROUTE_LOCAL_REPLY:
            answer = prompts.try_call_fast_agent(prompt, gateway_message, task=prompts.FAST_AGENT_TASK_REPLY)
            deliver_reply(prompts.ensure_reply_suffix(answer or route.reply))
            return route.kind

        if route.kind == ROUTE_FAST_AGENT:
            if notify_working:
                notify_working()
            decision = prompts.try_call_fast_agent_decision(prompt, gateway_message)
            if decision:
                print(f"Fast triage: {decision.route}")
            if decision and decision.route == prompts.FAST_DECISION_REPLY_NOW and decision.reply:
                deliver_reply(prompts.ensure_reply_suffix(decision.reply))
                return route.kind
            if decision and decision.route == prompts.FAST_DECISION_SEND_TO_DEEP:
                self.handoff_to_deep_agent(
                    conversation_id,
                    prompt,
                    gateway_message,
                    deliver_reply,
                    notify_working,
                    wait_reply=decision.reply,
                )
                return route.kind
            print("Fast agent khong co cau tra loi truc tiep hop le, chuyen sang deep agent.", file=sys.stderr)

        prompts.delay_before_deep_agent_if_needed(route.kind)
        self.handoff_to_deep_agent(conversation_id, prompt, gateway_message, deliver_reply, notify_working)
        return route.kind


__all__ = [
    "AGENT_MODE_SINGLE",
    "AGENT_MODE_TWO_AGENT",
    "ChatReplyGraph",
    "DeepAgentJob",
    "NotifyCallback",
    "ReplyCallback",
    "two_agent_mode_enabled",
]
