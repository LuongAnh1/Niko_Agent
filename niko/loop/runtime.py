"""LoopRuntime V0 cho các workflow cần observe, decide, act bằng tool.

Controller chỉ trả `LoopDecision`: gọi tool hoặc kết thúc bằng final reply.
Runtime mới là nơi gọi `ToolRegistry`, ghi observer events, gom tool calls và
chặn vòng lặp bằng `max_iterations`. File này không biết Telegram, memory hay
Jira policy; domain workflow bọc runtime và quyết định prompt, fallback, reply.

Ba invariant cần giữ: controller không tự mutate state; lỗi tool thành
`ToolResult`/`LoopResult` thay vì làm crash caller; lỗi observer bị nuốt để
trace/dashboard không phá luồng chính.
"""

from __future__ import annotations

from typing import Any

from niko.loop.observer import LoopObserver, NoopLoopObserver
from niko.loop.registry import ToolRegistry
from niko.loop.types import LoopController, LoopDecision, LoopResult, ToolContext, ToolResult


DEFAULT_LOOP_FALLBACK_REPLY = "Dạ em chưa hoàn tất vòng xử lý tool, anh thử lại giúp em nhé."


class LoopRuntime:
    """Điều khiển một vòng tool-use ngắn theo contract domain-agnostic."""

    def __init__(
        self,
        controller: LoopController,
        tool_registry: ToolRegistry,
        *,
        max_iterations: int = 3,
        observer: LoopObserver | None = None,
        fallback_reply: str = DEFAULT_LOOP_FALLBACK_REPLY,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self.controller = controller
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.observer = observer or NoopLoopObserver()
        self.fallback_reply = fallback_reply

    def run(self, prompt: str, context: ToolContext | None = None) -> LoopResult:
        context = context or ToolContext()
        history: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        self._event(
            "loop_started",
            {
                "max_iterations": self.max_iterations,
                "tool_count": len(self.tool_registry.schemas()),
                "conversation_id": context.conversation_id,
                "user_key": context.user_key,
            },
        )

        for iteration in range(1, self.max_iterations + 1):
            try:
                decision = self.controller(prompt, context, history, self.tool_registry.schemas())
            except Exception as exc:
                return self._error_result(str(exc), iteration=iteration, tool_calls=tool_calls)

            if not isinstance(decision, LoopDecision):
                return self._error_result(
                    f"Controller returned invalid decision: {type(decision).__name__}",
                    iteration=iteration,
                    tool_calls=tool_calls,
                )

            self._event(
                "loop_decision",
                {
                    "iteration": iteration,
                    "kind": decision.kind,
                    "tool_name": decision.tool_name,
                    "reason": decision.reason,
                },
            )
            history.append(
                {
                    "type": "decision",
                    "iteration": iteration,
                    "kind": decision.kind,
                    "tool_name": decision.tool_name,
                    "tool_args": decision.tool_args,
                    "reply": decision.reply,
                    "reason": decision.reason,
                }
            )

            if decision.kind == "final":
                self._event("loop_final_answer", {"iteration": iteration, "reply": decision.reply})
                return LoopResult(reply=decision.reply, tool_calls=tool_calls, iterations=iteration)

            if decision.kind != "tool":
                return self._error_result(
                    f"Unknown loop decision kind: {decision.kind}",
                    iteration=iteration,
                    tool_calls=tool_calls,
                )

            call_record = self._execute_tool(decision, context, iteration)
            tool_calls.append(call_record)
            history.append({"type": "tool_result", **call_record})

        self._event(
            "loop_limit_reached",
            {"iterations": self.max_iterations, "tool_call_count": len(tool_calls)},
        )
        return LoopResult(
            reply=self.fallback_reply,
            tool_calls=tool_calls,
            iterations=self.max_iterations,
            limit_reached=True,
        )

    def _execute_tool(self, decision: LoopDecision, context: ToolContext, iteration: int) -> dict[str, Any]:
        tool = self.tool_registry.get(decision.tool_name)
        mutates_state = bool(tool.mutates_state) if tool is not None else False
        self._event(
            "loop_tool_call_started",
            {
                "iteration": iteration,
                "tool_name": decision.tool_name,
                "tool_args": decision.tool_args,
                "mutates_state": mutates_state,
            },
        )
        result = self.tool_registry.execute(decision.tool_name, decision.tool_args, context)
        call_record = self._tool_call_record(decision, result, iteration, mutates_state)
        self._event("loop_tool_call_finished", call_record)
        return call_record

    @staticmethod
    def _tool_call_record(
        decision: LoopDecision,
        result: ToolResult,
        iteration: int,
        mutates_state: bool,
    ) -> dict[str, Any]:
        return {
            "iteration": iteration,
            "tool_name": decision.tool_name,
            "tool_args": decision.tool_args,
            "mutates_state": mutates_state,
            "ok": result.ok,
            "text": result.text,
            "data": result.data,
            "error": result.error,
        }

    def _error_result(self, error: str, *, iteration: int, tool_calls: list[dict[str, Any]]) -> LoopResult:
        self._event("loop_error", {"iteration": iteration, "error": error})
        return LoopResult(
            reply=self.fallback_reply,
            tool_calls=tool_calls,
            iterations=iteration,
            error=error,
        )

    def _event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        try:
            self.observer.event(kind, data or {})
        except Exception:
            pass
