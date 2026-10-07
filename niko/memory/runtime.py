"""Khung điều phối chat memory của Niko.

`MemoryRuntime` là cổng chính giữa Deep runtime và memory store. Lớp này gom
policy retrieval, gate bằng decision model, retrieval modes, search/list store,
write gate và consolidation vào một pipeline rõ ràng.

Consolidation có hai đường: dashboard/API thủ công và auto hook default-off sau
complete exchange. Auto hook chỉ spawn background worker, không chặn reply.

Luồng sửa/xóa memory đi qua `MemoryCorrectionWorkflow`. Runtime chỉ giữ facade để
ChatReplyGraph không phải biết chi tiết pending state, search target hay mutate DB.
"""

from __future__ import annotations

import threading
from typing import Callable

from bots.decision_model.memory import (
    MEMORY_DISCARD,
    MEMORY_REMEMBER,
    MEMORY_RETRIEVE,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_SKIP,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
    decide_memory_retrieval,
    decide_memory_write,
)
from niko.config import env_flag, env_value
from niko.memory.consolidation import ConsolidationBatch, ConsolidationResult, ConsolidationRunResult, MemoryConsolidator
from niko.memory.correction_workflow import MemoryCorrectionDecider, MemoryCorrectionResult, MemoryCorrectionWorkflow
from niko.memory.context import (
    RetrievedMemory,
    compact_episode_summary,
    format_memory_context,
    log_memory_gate_decision,
    log_memory_gate_error,
    memory_gate_enabled,
    memory_long_term_char_budget,
    memory_recent_char_budget,
    memory_recent_turns,
    memory_retrieval_enabled,
    memory_top_k,
    memory_write_enabled,
    memory_write_gate_enabled,
)
from niko.memory.store import MemoryStore, default_memory_store, utc_now
from niko.memory.working_memory import recent_chat_window


MemoryRetrievalDecider = Callable[[str, object | None], MemoryRetrievalDecision]
MemoryWriteDecider = Callable[[str, str, list[str] | None, object | None, str], MemoryWriteDecision]

AUTO_CONSOLIDATION_SKIP_ROUTES = {"deep_agent_wait", "busy_reply"}
DEFAULT_CONSOLIDATION_EXCHANGE_THRESHOLD = 6


def memory_consolidation_auto_enabled() -> bool:
    """Auto consolidation default-off để demo không bất ngờ xử lý backlog cũ."""
    return memory_write_enabled() and env_flag("NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED", "0")


def memory_consolidation_exchange_threshold() -> int:
    """Số complete exchange cần đủ trước khi auto consolidation chạy một batch."""
    raw_value = env_value(
        "NIKO_MEMORY_CONSOLIDATE_EVERY_N_EXCHANGES",
        str(DEFAULT_CONSOLIDATION_EXCHANGE_THRESHOLD),
    ).strip()
    try:
        return max(1, min(50, int(raw_value)))
    except ValueError:
        return DEFAULT_CONSOLIDATION_EXCHANGE_THRESHOLD


class MemoryRuntime:
    """Cổng memory trung tâm cho retrieval, write, correction và consolidation."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        retrieval_decider: MemoryRetrievalDecider | None = None,
        write_decider: MemoryWriteDecider | None = None,
        correction_decider: MemoryCorrectionDecider | None = None,
        consolidator: MemoryConsolidator | None = None,
    ) -> None:
        self._store = store
        self._retrieval_decider = retrieval_decider
        self._write_decider = write_decider
        self._correction_decider = correction_decider
        self._consolidator = consolidator
        self._auto_consolidation_lock = threading.Lock()
        self._correction_workflow = MemoryCorrectionWorkflow(
            store_provider=lambda: self.store,
            correction_decider=correction_decider,
        )

    @property
    def store(self) -> MemoryStore:
        """Dùng store explicit khi test; runtime mặc định tự lấy store theo config hiện tại."""
        return self._store or default_memory_store()

    @property
    def consolidator(self) -> MemoryConsolidator:
        """Consolidation đi qua runtime để giữ một cổng thống nhất."""
        return self._consolidator or MemoryConsolidator(store=self.store)

    def retrieve_for_deep(self, prompt: str, gateway_message=None) -> RetrievedMemory:
        """Trả context memory đã format và metadata trace cho Deep agent."""
        if not memory_retrieval_enabled():
            return RetrievedMemory(text="", facts=[], episodes=[], enabled=False)

        top_k = memory_top_k()
        gate = self.evaluate_retrieval_gate(prompt, gateway_message)
        recent_turns = self._recent_turns_for_deep(prompt, gateway_message)
        long_term_budget = memory_long_term_char_budget()
        recent_budget = memory_recent_char_budget()

        if gate["decision"] == MEMORY_SKIP:
            text = format_memory_context(
                [],
                [],
                gateway_message=gateway_message,
                recent_turns=recent_turns,
                long_term_char_budget=long_term_budget,
            )
            return self._build_result(
                text,
                [],
                [],
                gate,
                recent_turns=recent_turns,
                recent_char_budget=recent_budget,
                long_term_char_budget=long_term_budget,
            )

        search_query = gate["query"] or prompt
        facts = self._retrieve_facts(search_query, top_k, str(gate["fact_mode"]))
        episodes = self._retrieve_episodes(search_query, top_k, str(gate["episode_mode"]))
        text = format_memory_context(
            facts,
            episodes,
            gateway_message=gateway_message,
            recent_turns=recent_turns,
            long_term_char_budget=long_term_budget,
        )
        return self._build_result(
            text,
            facts,
            episodes,
            gate,
            recent_turns=recent_turns,
            recent_char_budget=recent_budget,
            long_term_char_budget=long_term_budget,
        )

    def build_context_for_deep(self, prompt: str, gateway_message=None) -> str:
        """Wrapper tiện dụng cho caller chỉ cần text prompt context."""
        return self.retrieve_for_deep(prompt, gateway_message=gateway_message).text

    def preview_consolidation_batch(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationBatch:
        """Preview read-only batch kế tiếp; dùng cho Refresh batch trên dashboard."""
        return self.consolidator.preview_next_batch(limit=limit, session_id=session_id)

    def mark_consolidation_batch(
        self,
        row_ids: list[int] | tuple[int, ...],
        reason: str = "manual",
    ) -> ConsolidationResult:
        """Đánh dấu batch đã xử lý sau khi caller manual/auto có guardrail riêng."""
        return self.consolidator.mark_batch_done(row_ids, reason=reason)

    def run_consolidation_once(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationRunResult:
        """Chạy consolidation thủ công đúng một batch, không có scheduler nền."""
        return self.consolidator.run_once(limit=limit, session_id=session_id)

    def run_auto_consolidation_once(
        self,
        conversation_id: str,
        trace_id: str = "",
        trace_logger=None,
    ) -> ConsolidationRunResult:
        """Chạy auto consolidation một batch nếu conversation đã đủ complete exchange."""
        threshold = memory_consolidation_exchange_threshold()
        batch = self.consolidator.preview_complete_exchange_batch(threshold, session_id=conversation_id)
        if batch.is_empty:
            data = {
                "conversation_id": conversation_id,
                "exchange_threshold": threshold,
                "reason": "insufficient_complete_exchanges",
            }
            self._trace_consolidation_event(trace_logger, trace_id, "memory_consolidation_auto_skipped", data)
            log_memory_consolidation_auto(
                "memory_consolidation_auto_skipped",
                "Auto consolidation skipped: insufficient complete exchanges.",
                data=data,
            )
            return ConsolidationRunResult("empty", batch, [], [], [], [], 0)

        start_data = {
            "conversation_id": conversation_id,
            "exchange_threshold": threshold,
            "row_ids": batch.row_ids,
            "rows_read": batch.rows_read,
        }
        self._trace_consolidation_event(trace_logger, trace_id, "memory_consolidation_auto_started", start_data)
        log_memory_consolidation_auto(
            "memory_consolidation_auto_started",
            "Auto consolidation started.",
            data=start_data,
        )

        result = self.consolidator.run_complete_exchange_once(threshold, session_id=conversation_id)
        result_data = self._consolidation_result_data(result, conversation_id, threshold)
        event = "memory_consolidation_auto_error" if result.status == "error" else "memory_consolidation_auto_finished"
        level = "warning" if result.status == "error" else "info"
        self._trace_consolidation_event(trace_logger, trace_id, event, result_data)
        log_memory_consolidation_auto(
            event,
            f"Auto consolidation finished: status={result.status}, marked={result.marked_count}.",
            level=level,
            data=result_data,
        )
        return result

    def record_chat_log(
        self,
        conversation_id: str,
        role: str,
        content: str,
        gateway_message,
        route: str,
        trace_id: str,
        trace_logger,
        meta: dict | None = None,
    ) -> None:
        """Ghi operational chat log; write gate không chặn lớp log dùng để debug."""
        if not memory_write_enabled():
            return
        metadata = {
            "route": route,
            "trace_id": trace_id,
            "user_key": gateway_message.user.key,
            "chat_id": gateway_message.chat_id,
            "chat_type": gateway_message.chat_type,
        }
        if meta:
            metadata.update(meta)
        try:
            row_id = self.store.log_chat(
                conversation_id,
                role,
                content,
                source=gateway_message.platform or "chat",
                meta=metadata,
            )
            trace_logger.event(trace_id, "memory_write_chat_log", {"row_id": row_id, "role": role, "route": route})
            self.maybe_start_auto_consolidation(conversation_id, role, route, trace_id, trace_logger)
        except Exception as exc:
            trace_logger.event(trace_id, "memory_write_error", {"target": "chat_log", "error": str(exc)})

    def maybe_start_auto_consolidation(
        self,
        conversation_id: str,
        role: str,
        route: str,
        trace_id: str,
        trace_logger,
    ) -> None:
        """Sau assistant reply thật, thử bật worker auto consolidation nếu config cho phép."""
        if not memory_consolidation_auto_enabled():
            return
        if not self._is_completed_assistant_reply(role, route):
            return
        if not self._auto_consolidation_lock.acquire(blocking=False):
            data = {"conversation_id": conversation_id, "reason": "already_running"}
            self._trace_consolidation_event(trace_logger, trace_id, "memory_consolidation_auto_skipped", data)
            log_memory_consolidation_auto(
                "memory_consolidation_auto_skipped",
                "Auto consolidation skipped: worker already running.",
                data=data,
            )
            return

        try:
            self._start_auto_consolidation_thread(conversation_id, trace_id, trace_logger)
        except Exception:
            self._auto_consolidation_lock.release()
            raise

    def _start_auto_consolidation_thread(self, conversation_id: str, trace_id: str, trace_logger) -> None:
        """Start worker riêng để auto consolidation không khóa đường trả lời Telegram."""
        thread = threading.Thread(
            target=self._run_auto_consolidation_worker,
            args=(conversation_id, trace_id, trace_logger),
            daemon=True,
        )
        thread.start()

    def record_deep_episode(
        self,
        conversation_id: str,
        prompt: str,
        answer: str,
        gateway_message,
        followups: list[str] | None,
        trace_id: str,
        trace_logger,
        route: str = "deep_agent",
    ) -> None:
        """Ghi episodic memory sau Deep job, có write gate nếu được bật."""
        if not memory_write_enabled():
            return
        followups = followups or []
        gate = self.evaluate_write_gate(prompt, answer, followups, gateway_message, route)
        self._trace_write_gate(trace_logger, trace_id, gate)
        if gate["decision"] == MEMORY_DISCARD:
            return

        summary = compact_episode_summary(prompt, answer, followups=followups)
        try:
            episode_id = self.store.add_episode(
                summary,
                happened_at=utc_now(),
                source="niko_deep",
                meta={
                    "conversation_id": conversation_id,
                    "trace_id": trace_id,
                    "user_key": gateway_message.user.key,
                    "followups": followups,
                },
            )
            data = {"episode_id": episode_id}
            if gate["enabled"]:
                data["write_gate"] = self._gate_event_data(gate)
            trace_logger.event(trace_id, "memory_write_episode", data)
        except Exception as exc:
            trace_logger.event(trace_id, "memory_write_error", {"target": "episodes", "error": str(exc)})

    def handle_memory_correction(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        trace_id: str,
        trace_logger,
    ) -> MemoryCorrectionResult:
        """Facade để chat graph gọi correction mà không biết chi tiết workflow."""
        return self._correction_workflow.handle(
            conversation_id,
            prompt,
            gateway_message,
            trace_id,
            trace_logger,
        )

    def evaluate_retrieval_gate(self, prompt: str, gateway_message=None) -> dict[str, object]:
        """Chạy gate nếu bật; lỗi gate fail-open để Deep vẫn có cơ hội dùng memory."""
        gate_state: dict[str, object] = {
            "enabled": memory_gate_enabled(),
            "decision": "",
            "query": "",
            "reason": "",
            "fact_mode": MEMORY_RETRIEVAL_SEARCH,
            "episode_mode": MEMORY_RETRIEVAL_SEARCH,
            "confidence": None,
            "label": "",
            "probabilities": {},
            "model": "",
            "error": "",
        }
        if not gate_state["enabled"]:
            return gate_state

        try:
            decision = self._decide_retrieval(prompt, gateway_message)
        except Exception as exc:
            gate_state.update(
                {
                    "decision": MEMORY_RETRIEVE,
                    "query": prompt,
                    "reason": "gate_error_fail_open",
                    "fact_mode": MEMORY_RETRIEVAL_SEARCH,
                    "episode_mode": MEMORY_RETRIEVAL_SEARCH,
                    "error": str(exc),
                }
            )
            log_memory_gate_error(gate_state)
            return gate_state

        gate_state.update(
            {
                "decision": decision.decision,
                "query": decision.query if decision.decision == MEMORY_RETRIEVE else "",
                "reason": decision.reason,
                "fact_mode": decision.fact_mode if decision.decision == MEMORY_RETRIEVE else MEMORY_RETRIEVAL_NONE,
                "episode_mode": decision.episode_mode if decision.decision == MEMORY_RETRIEVE else MEMORY_RETRIEVAL_NONE,
                "confidence": decision.confidence,
                "label": decision.label,
                "probabilities": decision.probabilities,
                "model": decision.model,
            }
        )
        log_memory_gate_decision(gate_state)
        return gate_state

    def evaluate_write_gate(
        self,
        prompt: str,
        answer: str,
        followups: list[str] | None,
        gateway_message=None,
        route: str = "",
    ) -> dict[str, object]:
        """Chạy write gate nếu bật; lỗi gate fail-open để không mất episode baseline."""
        gate_state: dict[str, object] = {
            "enabled": memory_write_gate_enabled(),
            "decision": "",
            "reason": "",
            "confidence": None,
            "label": "",
            "probabilities": {},
            "model": "",
            "error": "",
        }
        if not gate_state["enabled"]:
            return gate_state

        try:
            decision = self._decide_write(prompt, answer, followups, gateway_message, route)
        except Exception as exc:
            gate_state.update(
                {
                    "decision": MEMORY_REMEMBER,
                    "reason": "write_gate_error_fail_open",
                    "error": str(exc),
                }
            )
            log_memory_write_gate_error(gate_state)
            log_memory_write_decision(gate_state)
            return gate_state

        gate_state.update(
            {
                "decision": decision.decision,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "label": decision.label,
                "probabilities": decision.probabilities,
                "model": decision.model,
            }
        )
        log_memory_write_decision(gate_state)
        return gate_state

    def evaluate_correction_intent(
        self,
        prompt: str,
        gateway_message=None,
        decision_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Compatibility wrapper cho test/caller cũ; implementation nằm ở workflow."""
        return self._correction_workflow.evaluate_intent(prompt, gateway_message, decision_context)

    def _run_auto_consolidation_worker(self, conversation_id: str, trace_id: str, trace_logger) -> None:
        try:
            self.run_auto_consolidation_once(conversation_id, trace_id=trace_id, trace_logger=trace_logger)
        except Exception as exc:
            data = {"conversation_id": conversation_id, "error": str(exc)}
            self._trace_consolidation_event(trace_logger, trace_id, "memory_consolidation_auto_error", data)
            log_memory_consolidation_auto(
                "memory_consolidation_auto_error",
                f"Auto consolidation loi: {exc}",
                level="warning",
                data=data,
            )
        finally:
            self._auto_consolidation_lock.release()

    @staticmethod
    def _is_completed_assistant_reply(role: str, route: str) -> bool:
        normalized_role = str(role or "").strip().lower()
        normalized_route = str(route or "").strip()
        return normalized_role == "assistant" and normalized_route not in AUTO_CONSOLIDATION_SKIP_ROUTES

    @staticmethod
    def _trace_consolidation_event(trace_logger, trace_id: str, event: str, data: dict[str, object]) -> None:
        if trace_logger is None:
            return
        try:
            trace_logger.event(trace_id, event, data)
        except Exception:
            pass

    @staticmethod
    def _consolidation_result_data(
        result: ConsolidationRunResult,
        conversation_id: str,
        threshold: int,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "conversation_id": conversation_id,
            "exchange_threshold": threshold,
            "status": result.status,
            "rows_read": result.batch.rows_read,
            "row_ids": result.batch.row_ids,
            "facts_written": result.facts_written,
            "episodes_written": result.episodes_written,
            "marked_count": result.marked_count,
        }
        if result.error:
            data["error"] = result.error
        return data

    def _decide_retrieval(self, prompt: str, gateway_message=None) -> MemoryRetrievalDecision:
        decider = self._retrieval_decider or decide_memory_retrieval
        return decider(prompt, gateway_message)

    def _decide_write(
        self,
        prompt: str,
        answer: str,
        followups: list[str] | None,
        gateway_message=None,
        route: str = "",
    ) -> MemoryWriteDecision:
        decider = self._write_decider or decide_memory_write
        return decider(prompt, answer, followups, gateway_message, route)

    def _retrieve_facts(self, search_query: str, top_k: int, fact_mode: str):
        """Thực thi fact retrieval theo mode mà decision model đã chọn."""
        if fact_mode == MEMORY_RETRIEVAL_NONE:
            return []
        if fact_mode == MEMORY_RETRIEVAL_LIST:
            return self.store.list_facts(top_k)
        return self.store.search_facts(search_query, top_k=top_k)

    def _retrieve_episodes(self, search_query: str, top_k: int, episode_mode: str):
        """Thực thi episode retrieval theo mode mà decision model đã chọn."""
        if episode_mode == MEMORY_RETRIEVAL_NONE:
            return []
        if episode_mode == MEMORY_RETRIEVAL_RECENT:
            return self.store.recent_episodes(top_k)
        return self.store.search_episodes(search_query, top_k=top_k)

    def _trace_write_gate(self, trace_logger, trace_id: str, gate: dict[str, object]) -> None:
        if not gate["enabled"]:
            return
        if gate["error"]:
            trace_logger.event(trace_id, "memory_write_gate_error", self._gate_event_data(gate))
        trace_logger.event(trace_id, "memory_write_decision", self._gate_event_data(gate))

    def _recent_turns_for_deep(self, prompt: str, gateway_message=None) -> list[dict[str, str]]:
        """Lấy recent conversation cho Deep, không phụ thuộc long-term retrieval gate."""
        if gateway_message is None:
            return []
        conversation_id = str(getattr(gateway_message, "chat_id", "") or getattr(gateway_message.user, "key", ""))
        if not conversation_id:
            return []
        return recent_chat_window(
            self.store,
            conversation_id,
            prompt,
            limit=memory_recent_turns(),
            char_budget=memory_recent_char_budget(),
            per_turn_limit=700,
        )

    @staticmethod
    def _gate_event_data(gate: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in gate.items() if value not in ("", None, {})}

    @staticmethod
    def _build_result(
        text: str,
        facts,
        episodes,
        gate: dict[str, object],
        *,
        recent_turns: list[dict[str, str]] | None = None,
        recent_char_budget: int = 0,
        long_term_char_budget: int = 0,
    ) -> RetrievedMemory:
        return RetrievedMemory(
            text=text,
            facts=facts,
            episodes=episodes,
            enabled=True,
            gate_enabled=bool(gate["enabled"]),
            gate_decision=str(gate["decision"]),
            gate_query=str(gate["query"]),
            gate_reason=str(gate["reason"]),
            gate_fact_mode=str(gate["fact_mode"]),
            gate_episode_mode=str(gate["episode_mode"]),
            gate_confidence=gate["confidence"] if isinstance(gate["confidence"], float) else None,
            gate_label=str(gate["label"]),
            gate_probabilities=gate["probabilities"] if isinstance(gate["probabilities"], dict) else {},
            gate_model=str(gate["model"]),
            gate_error=str(gate["error"]),
            recent_turns=recent_turns or [],
            recent_char_budget=recent_char_budget,
            long_term_char_budget=long_term_char_budget,
        )


def log_memory_write_decision(gate_state: dict[str, object]) -> None:
    """Ghi log write gate cho tab Bots; lỗi log không được ảnh hưởng turn."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_write_decision",
            (
                "Memory write gate: "
                f"decision={gate_state.get('decision') or 'disabled'} "
                f"reason={gate_state.get('reason') or '-'}"
            ),
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def log_memory_consolidation_auto(
    event: str,
    message: str,
    *,
    level: str = "info",
    data: dict[str, object] | None = None,
) -> None:
    """Ghi runtime log cho auto consolidation; lỗi log không ảnh hưởng chat turn."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            event,
            message,
            level=level,
            data=data or {},
        )
    except Exception:
        pass


def log_memory_write_gate_error(gate_state: dict[str, object]) -> None:
    """Write gate lỗi thì chỉ log; caller fail-open để vẫn ghi episode baseline."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_write_gate_error",
            f"Memory write gate loi, fail-open episode write: {gate_state.get('error')}",
            level="warning",
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


_DEFAULT_MEMORY_RUNTIME: MemoryRuntime | None = None
_DEFAULT_MEMORY_RUNTIME_LOCK = threading.Lock()


def default_memory_runtime() -> MemoryRuntime:
    """Singleton nhẹ cho pipeline; store bên trong vẫn đọc theo runtime config hiện tại."""
    global _DEFAULT_MEMORY_RUNTIME
    with _DEFAULT_MEMORY_RUNTIME_LOCK:
        if _DEFAULT_MEMORY_RUNTIME is None:
            _DEFAULT_MEMORY_RUNTIME = MemoryRuntime()
        return _DEFAULT_MEMORY_RUNTIME


__all__ = [
    "MemoryCorrectionDecider",
    "MemoryCorrectionResult",
    "MemoryRuntime",
    "MemoryRetrievalDecider",
    "MemoryWriteDecider",
    "default_memory_runtime",
    "memory_consolidation_auto_enabled",
    "memory_consolidation_exchange_threshold",
]
