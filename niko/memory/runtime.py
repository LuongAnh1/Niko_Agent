"""Khung điều phối chat memory của Niko.

`MemoryRuntime` là cổng chính giữa Deep runtime và memory store. Lớp này gom
policy retrieval, gate bằng decision model, inventory question, search store và
format context vào một pipeline rõ ràng để các bước sau như write gate hay
consolidation có chỗ cắm ổn định hơn.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from bots.decision_model.memory import (
    MEMORY_DISCARD,
    MEMORY_REMEMBER,
    MEMORY_RETRIEVE,
    MEMORY_SKIP,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
    decide_memory_retrieval,
    decide_memory_write,
)
from niko.memory.consolidation import ConsolidationBatch, ConsolidationResult, ConsolidationRunResult, MemoryConsolidator
from niko.memory.context import (
    RetrievedMemory,
    asks_for_episode_inventory,
    asks_for_fact_inventory,
    compact_episode_summary,
    fact_inventory_filter_words,
    format_memory_context,
    log_memory_gate_decision,
    log_memory_gate_error,
    memory_gate_enabled,
    memory_retrieval_enabled,
    memory_top_k,
    memory_write_enabled,
    memory_write_gate_enabled,
)
from niko.memory.store import MemoryStore, default_memory_store, utc_now


MemoryRetrievalDecider = Callable[[str, object | None], MemoryRetrievalDecision]
MemoryWriteDecider = Callable[[str, str, list[str] | None, object | None, str], MemoryWriteDecision]


class MemoryRuntime:
    """Pipeline memory cho một Niko instance, tách khỏi Claude CLI runtime."""

    def __init__(
        self,
        store: MemoryStore | None = None,
        retrieval_decider: MemoryRetrievalDecider | None = None,
        write_decider: MemoryWriteDecider | None = None,
        consolidator: MemoryConsolidator | None = None,
    ) -> None:
        self._store = store
        self._retrieval_decider = retrieval_decider
        self._write_decider = write_decider
        self._consolidator = consolidator

    @property
    def store(self) -> MemoryStore:
        """Dùng store explicit khi test; runtime mặc định tự lấy store theo config hiện tại."""
        return self._store or default_memory_store()

    @property
    def consolidator(self) -> MemoryConsolidator:
        """Consolidation đi qua runtime để giữ một cổng memory thống nhất."""
        return self._consolidator or MemoryConsolidator(store=self.store)

    def retrieve_for_deep(self, prompt: str, gateway_message=None) -> RetrievedMemory:
        """Trả context memory đã format và metadata trace cho Deep agent."""
        if not memory_retrieval_enabled():
            return RetrievedMemory(text="", facts=[], episodes=[], enabled=False)

        top_k = memory_top_k()
        fact_inventory = asks_for_fact_inventory(prompt)
        episode_inventory = asks_for_episode_inventory(prompt)
        gate = self.evaluate_retrieval_gate(prompt, gateway_message, bypass=fact_inventory or episode_inventory)

        if gate["decision"] == MEMORY_SKIP:
            return self._build_result("", [], [], gate)

        search_query = gate["query"] or prompt
        facts = self._retrieve_facts(prompt, search_query, top_k, fact_inventory)
        episodes = self._retrieve_episodes(search_query, top_k, fact_inventory, episode_inventory)
        text = format_memory_context(facts, episodes, gateway_message=gateway_message)
        return self._build_result(text, facts, episodes, gate)

    def build_context_for_deep(self, prompt: str, gateway_message=None) -> str:
        """Wrapper tiện dụng cho caller chỉ cần text prompt context."""
        return self.retrieve_for_deep(prompt, gateway_message=gateway_message).text

    def preview_consolidation_batch(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationBatch:
        """Xem batch chat log kế tiếp mà chưa ghi fact/episode hay mark consolidated."""
        return self.consolidator.preview_next_batch(limit=limit, session_id=session_id)

    def mark_consolidation_batch(
        self,
        row_ids: list[int] | tuple[int, ...],
        reason: str = "manual",
    ) -> ConsolidationResult:
        """Đánh dấu batch đã xử lý; phase sau sẽ gọi sau khi candidate hợp lệ."""
        return self.consolidator.mark_batch_done(row_ids, reason=reason)

    def run_consolidation_once(
        self,
        limit: int | None = None,
        session_id: str | None = None,
    ) -> ConsolidationRunResult:
        """Chạy consolidation thủ công một lần, không có scheduler nền."""
        return self.consolidator.run_once(limit=limit, session_id=session_id)

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
        except Exception as exc:
            trace_logger.event(trace_id, "memory_write_error", {"target": "chat_log", "error": str(exc)})

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

    def evaluate_retrieval_gate(self, prompt: str, gateway_message=None, *, bypass: bool = False) -> dict[str, object]:
        """Chạy gate nếu bật; lỗi gate fail-open để Deep vẫn có cơ hội dùng memory."""
        gate_state: dict[str, object] = {
            "enabled": memory_gate_enabled(),
            "decision": "",
            "query": "",
            "reason": "",
            "confidence": None,
            "label": "",
            "probabilities": {},
            "model": "",
            "error": "",
        }
        if not gate_state["enabled"]:
            return gate_state

        if bypass:
            gate_state.update(
                {
                    "decision": MEMORY_RETRIEVE,
                    "query": prompt,
                    "reason": "inventory_question_bypasses_gate",
                    "label": "inventory_bypass",
                }
            )
            log_memory_gate_decision(gate_state)
            return gate_state

        try:
            decision = self._decide_retrieval(prompt, gateway_message)
        except Exception as exc:
            gate_state.update(
                {
                    "decision": MEMORY_RETRIEVE,
                    "query": prompt,
                    "reason": "gate_error_fail_open",
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

    def _retrieve_facts(self, prompt: str, search_query: str, top_k: int, fact_inventory: bool):
        # Câu hỏi "đang lưu fact nào" cần list inventory, không search theo chữ "fact".
        if fact_inventory and not fact_inventory_filter_words(prompt):
            return self.store.list_facts(top_k)
        return self.store.search_facts(search_query, top_k=top_k)

    def _retrieve_episodes(self, search_query: str, top_k: int, fact_inventory: bool, episode_inventory: bool):
        if episode_inventory:
            return self.store.recent_episodes(top_k)
        if fact_inventory:
            return []
        return self.store.search_episodes(search_query, top_k=top_k)

    def _trace_write_gate(self, trace_logger, trace_id: str, gate: dict[str, object]) -> None:
        if not gate["enabled"]:
            return
        if gate["error"]:
            trace_logger.event(trace_id, "memory_write_gate_error", self._gate_event_data(gate))
        trace_logger.event(trace_id, "memory_write_decision", self._gate_event_data(gate))

    @staticmethod
    def _gate_event_data(gate: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in gate.items() if value not in ("", None, {})}

    @staticmethod
    def _build_result(text: str, facts, episodes, gate: dict[str, object]) -> RetrievedMemory:
        return RetrievedMemory(
            text=text,
            facts=facts,
            episodes=episodes,
            enabled=True,
            gate_enabled=bool(gate["enabled"]),
            gate_decision=str(gate["decision"]),
            gate_query=str(gate["query"]),
            gate_reason=str(gate["reason"]),
            gate_confidence=gate["confidence"] if isinstance(gate["confidence"], float) else None,
            gate_label=str(gate["label"]),
            gate_probabilities=gate["probabilities"] if isinstance(gate["probabilities"], dict) else {},
            gate_model=str(gate["model"]),
            gate_error=str(gate["error"]),
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


__all__ = ["MemoryRuntime", "MemoryRetrievalDecider", "MemoryWriteDecider", "default_memory_runtime"]
