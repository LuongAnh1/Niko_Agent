"""Khung điều phối chat memory của Niko.

`MemoryRuntime` là cổng chính giữa Deep runtime và memory store. Lớp này gom
policy retrieval, gate bằng decision model, retrieval modes, search/list store và
format context vào một pipeline rõ ràng để các bước sau như write gate hay
consolidation có chỗ cắm ổn định hơn.

Luồng sửa/xóa memory trong Phase 5 V1 chỉ là baseline tạm thời cho chat: runtime
giữ pending fact IDs trong RAM, validate lựa chọn của user, rồi mới update/delete
SQLite. Khi Loop/tool slot hoàn chỉnh hơn, phần xác nhận và mutate memory nên
được chuyển thành workflow/tool riêng thay vì mở rộng thêm trong chat graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any, Callable

from bots.decision_model.memory import (
    MEMORY_CORRECT_MEMORY,
    MEMORY_CORRECTION_NONE,
    MEMORY_DISCARD,
    MEMORY_FORGET_MEMORY,
    MEMORY_REMEMBER,
    MEMORY_RETRIEVE,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_SKIP,
    MEMORY_TARGET_EPISODE,
    MEMORY_TARGET_FACT,
    MEMORY_TARGET_UNKNOWN,
    MemoryCorrectionDecision,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
    decide_memory_correction_intent,
    decide_memory_retrieval,
    decide_memory_write,
)
from niko.memory.consolidation import ConsolidationBatch, ConsolidationResult, ConsolidationRunResult, MemoryConsolidator
from niko.memory.context import (
    RetrievedMemory,
    compact_episode_summary,
    format_memory_context,
    log_memory_gate_decision,
    log_memory_gate_error,
    memory_correction_detection_enabled,
    memory_gate_enabled,
    memory_retrieval_enabled,
    memory_top_k,
    memory_write_enabled,
    memory_write_gate_enabled,
)
from niko.memory.store import MemoryStore, default_memory_store, utc_now


MemoryRetrievalDecider = Callable[[str, object | None], MemoryRetrievalDecision]
MemoryWriteDecider = Callable[[str, str, list[str] | None, object | None, str], MemoryWriteDecision]
MemoryCorrectionDecider = Callable[..., MemoryCorrectionDecision]


@dataclass(frozen=True)
class MemoryCorrectionResult:
    """Kết quả xử lý yêu cầu sửa/xóa memory qua chat."""

    handled: bool
    reply: str = ""
    decision: dict[str, object] = field(default_factory=dict)
    route: str = "memory_correction"


@dataclass(frozen=True)
class PendingMemoryCorrection:
    """State RAM tạm cho V1 khi correction gate cần user chọn fact ID."""

    decision: str
    query: str
    replacement: str
    fact_ids: list[int]
    created_at: float


class MemoryRuntime:
    """Cổng memory trung tâm cho retrieval modes, write gate, chat log và consolidation."""

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
        self._pending_corrections: dict[str, PendingMemoryCorrection] = {}
        self._pending_corrections_lock = threading.RLock()

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
        gate = self.evaluate_retrieval_gate(prompt, gateway_message)

        if gate["decision"] == MEMORY_SKIP:
            return self._build_result("", [], [], gate)

        search_query = gate["query"] or prompt
        facts = self._retrieve_facts(search_query, top_k, str(gate["fact_mode"]))
        episodes = self._retrieve_episodes(search_query, top_k, str(gate["episode_mode"]))
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

    def handle_memory_correction(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        trace_id: str,
        trace_logger,
    ) -> MemoryCorrectionResult:
        """Xử lý yêu cầu sửa/xóa fact qua chat nếu correction gate nhận diện rõ."""
        decision_context = self._build_correction_decision_context(conversation_id, prompt)
        gate = self.evaluate_correction_intent(prompt, gateway_message, decision_context)
        if not gate["enabled"]:
            return MemoryCorrectionResult(handled=False, decision=gate)

        trace_logger.event(trace_id, "memory_correction_decision", self._gate_event_data(gate))
        if self._is_pending_fact_choice_prompt(conversation_id, prompt):
            pending_result = self._handle_pending_memory_correction(conversation_id, prompt, trace_id, trace_logger, gate)
            if pending_result is not None:
                return pending_result

        if gate["error"] or gate["decision"] == MEMORY_CORRECTION_NONE:
            pending_result = self._handle_pending_memory_correction(conversation_id, prompt, trace_id, trace_logger, gate)
            if pending_result is not None:
                return pending_result
            return MemoryCorrectionResult(handled=False, decision=gate)

        if gate["target_type"] == MEMORY_TARGET_EPISODE:
            reply = (
                "Dạ phần episode hiện tại em chỉ cho xem, chưa cho sửa hoặc xóa qua chat để tránh mất "
                "ngữ cảnh lịch sử. Nếu anh muốn, em sẽ xử lý phần này ở phase dashboard/ops sau."
            )
            self._trace_memory_correction_clarify(trace_logger, trace_id, gate, "episode_read_only")
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        facts = self._find_correction_facts(prompt, str(gate["query"]))
        if not facts:
            reply = "Dạ em chưa tìm thấy fact nào khớp rõ với yêu cầu này, nên em chưa sửa/xóa gì cả anh."
            self._trace_memory_correction_clarify(trace_logger, trace_id, gate, "no_fact_match")
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        if len(facts) > 1:
            self._remember_pending_correction(conversation_id, gate, [fact.id for fact in facts])
            reply = self._build_ambiguous_fact_reply(facts)
            self._trace_memory_correction_clarify(
                trace_logger,
                trace_id,
                gate,
                "ambiguous_fact_match",
                fact_ids=[fact.id for fact in facts],
            )
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        fact = facts[0]
        if gate["decision"] == MEMORY_FORGET_MEMORY:
            deleted = self.store.delete_fact(fact.id)
            if deleted:
                self._trace_memory_correction_applied(
                    trace_logger,
                    trace_id,
                    gate,
                    action="delete_fact",
                    fact_id=fact.id,
                    conversation_id=conversation_id,
                )
                return MemoryCorrectionResult(
                    handled=True,
                    reply=f"Dạ em đã xóa fact #{fact.id}: {fact.subject} - {fact.content}",
                    decision=gate,
                )
            reply = "Dạ fact này vừa không còn tồn tại trong memory, nên em chưa xóa thêm gì cả anh."
            self._trace_memory_correction_clarify(trace_logger, trace_id, gate, "fact_delete_missed", fact_ids=[fact.id])
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        if gate["decision"] == MEMORY_CORRECT_MEMORY:
            replacement = str(gate["replacement"]).strip()
            if not replacement:
                reply = "Dạ anh nói rõ nội dung mới cần thay cho fact đó giúp em nhé, hiện em chưa sửa gì cả."
                self._trace_memory_correction_clarify(trace_logger, trace_id, gate, "missing_replacement", fact_ids=[fact.id])
                return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

            meta = dict(fact.meta)
            meta["corrected_by"] = "chat"
            meta["correction_trace_id"] = trace_id
            meta["previous_content"] = fact.content
            updated = self.store.update_fact(fact.id, fact.subject, replacement, meta=meta)
            if updated:
                self._trace_memory_correction_applied(
                    trace_logger,
                    trace_id,
                    gate,
                    action="update_fact",
                    fact_id=fact.id,
                    conversation_id=conversation_id,
                )
                return MemoryCorrectionResult(
                    handled=True,
                    reply=f"Dạ em đã sửa fact #{fact.id} thành: {replacement}",
                    decision=gate,
                )

            reply = "Dạ fact này vừa không còn tồn tại trong memory, nên em chưa sửa gì cả anh."
            self._trace_memory_correction_clarify(trace_logger, trace_id, gate, "fact_update_missed", fact_ids=[fact.id])
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        return MemoryCorrectionResult(handled=False, decision=gate)

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
        """Chạy gate sửa/xóa memory; lỗi fail-closed để không mutate nhầm."""
        decision_context = decision_context or {}
        gate_state: dict[str, object] = {
            "enabled": memory_correction_detection_enabled(),
            "decision": MEMORY_CORRECTION_NONE,
            "query": "",
            "target_type": MEMORY_TARGET_UNKNOWN,
            "replacement": "",
            "reason": "",
            "confidence": None,
            "label": "",
            "probabilities": {},
            "model": "",
            "error": "",
            "recent_turn_count": len(decision_context.get("recent_turns", []))
            if isinstance(decision_context.get("recent_turns"), list)
            else 0,
            "active_workflow": str(decision_context.get("active_workflow", "") or ""),
            "pending_action": str(decision_context.get("pending_action", "") or ""),
            "pending_choices": decision_context.get("pending_choices", [])
            if isinstance(decision_context.get("pending_choices"), list)
            else [],
        }
        if not gate_state["enabled"]:
            return gate_state

        try:
            decision = self._decide_correction(prompt, gateway_message, decision_context)
        except Exception as exc:
            gate_state.update({"error": str(exc), "reason": "correction_gate_error_fail_closed"})
            log_memory_correction_error(gate_state)
            log_memory_correction_decision(gate_state)
            return gate_state

        gate_state.update(
            {
                "decision": decision.decision,
                "query": decision.query,
                "target_type": decision.target_type,
                "replacement": decision.replacement,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "label": decision.label,
                "probabilities": decision.probabilities,
                "model": decision.model,
            }
        )
        log_memory_correction_decision(gate_state)
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

    def _decide_correction(
        self,
        prompt: str,
        gateway_message=None,
        decision_context: dict[str, object] | None = None,
    ) -> MemoryCorrectionDecision:
        if self._correction_decider is None:
            return decide_memory_correction_intent(
                prompt,
                gateway_message,
                decision_context=decision_context,
            )
        try:
            return self._correction_decider(prompt, gateway_message, decision_context)
        except TypeError as exc:
            try:
                return self._correction_decider(prompt, gateway_message)
            except TypeError:
                raise exc

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

    def _build_correction_decision_context(self, conversation_id: str, prompt: str) -> dict[str, object]:
        """Gửi vài turn chat gần nhất là ngữ cảnh chính; pending chỉ là metadata phụ."""
        pending = self._get_pending_correction(conversation_id)
        context: dict[str, object] = {
            "recent_turns": self._recent_turns_for_decision(conversation_id, prompt, limit=6),
            "active_workflow": "",
            "pending_action": "",
            "pending_choices": [],
            "pending_replacement": "",
        }
        if pending is not None:
            context.update(
                {
                    "active_workflow": "memory_correction",
                    "pending_action": pending.decision,
                    "pending_choices": [{"type": MEMORY_TARGET_FACT, "id": fact_id} for fact_id in pending.fact_ids],
                    "pending_replacement": pending.replacement,
                }
            )
        return context

    def _recent_turns_for_decision(self, conversation_id: str, current_prompt: str, limit: int = 6) -> list[dict[str, str]]:
        """Lấy vài chat_log gần nhất, bỏ chính incoming prompt hiện tại nếu đã được ghi."""
        try:
            rows = self.store.chat_history(conversation_id, limit=limit + 2)
        except Exception:
            return []
        clean_rows = list(rows)
        if clean_rows:
            last = clean_rows[-1]
            if (
                str(last.get("role", "")).lower() == "user"
                and str(last.get("content", "")).strip() == current_prompt.strip()
            ):
                clean_rows = clean_rows[:-1]

        turns: list[dict[str, str]] = []
        for row in clean_rows[-limit:]:
            role = str(row.get("role", "")).strip()
            content = self._truncate_decision_text(str(row.get("content", "")))
            if not role or not content:
                continue
            turn = {
                "role": role,
                "content": content,
            }
            created_at = str(row.get("created_at", "")).strip()
            if created_at:
                turn["created_at"] = created_at
            meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
            route = str(meta.get("route", "")).strip()
            if route:
                turn["route"] = route
            turns.append(turn)
        return turns

    def _trace_write_gate(self, trace_logger, trace_id: str, gate: dict[str, object]) -> None:
        if not gate["enabled"]:
            return
        if gate["error"]:
            trace_logger.event(trace_id, "memory_write_gate_error", self._gate_event_data(gate))
        trace_logger.event(trace_id, "memory_write_decision", self._gate_event_data(gate))

    def _find_correction_facts(self, prompt: str, query: str):
        """Tìm fact bằng ID rõ ràng trước, sau đó mới dùng search text."""
        fact_id = self._extract_fact_id(prompt)
        if fact_id is not None:
            return [fact for fact in self.store.list_facts(limit=200) if fact.id == fact_id]
        search_query = query or prompt
        return self.store.search_facts(search_query, top_k=5)

    def _handle_pending_memory_correction(
        self,
        conversation_id: str,
        prompt: str,
        trace_id: str,
        trace_logger,
        base_gate: dict[str, object] | None = None,
    ) -> MemoryCorrectionResult | None:
        """Guardrail cuối nếu model bỏ sót reply chọn fact ID dù đã có recent_turns."""
        if not memory_correction_detection_enabled():
            return None
        fact_id = self._extract_fact_id(prompt)
        if fact_id is None:
            return None
        pending = self._get_pending_correction(conversation_id)
        if pending is None:
            return None

        gate: dict[str, object] = {
            "enabled": True,
            "decision": pending.decision,
            "query": pending.query,
            "target_type": MEMORY_TARGET_FACT,
            "replacement": pending.replacement,
            "reason": "pending_ambiguous_followup",
            "label": "pending_followup",
            "fact_ids": pending.fact_ids,
        }
        if base_gate:
            gate["model_decision"] = base_gate.get("decision")
            gate["model_label"] = base_gate.get("label")
        trace_logger.event(trace_id, "memory_correction_context_fallback", self._gate_event_data(gate))

        if fact_id not in pending.fact_ids:
            reply = self._build_pending_fact_mismatch_reply(pending.fact_ids)
            self._trace_memory_correction_clarify(
                trace_logger,
                trace_id,
                gate,
                "pending_fact_id_not_offered",
                fact_ids=pending.fact_ids,
            )
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        facts = [fact for fact in self.store.list_facts(limit=200) if fact.id == fact_id]
        if not facts:
            self._clear_pending_correction(conversation_id)
            reply = f"Dạ fact #{fact_id} hiện không còn tồn tại trong memory, nên em chưa sửa/xóa gì cả anh."
            self._trace_memory_correction_clarify(
                trace_logger,
                trace_id,
                gate,
                "pending_fact_missing",
                fact_ids=[fact_id],
            )
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        fact = facts[0]
        if pending.decision == MEMORY_FORGET_MEMORY:
            self._clear_pending_correction(conversation_id)
            deleted = self.store.delete_fact(fact.id)
            if deleted:
                self._trace_memory_correction_applied(
                    trace_logger,
                    trace_id,
                    gate,
                    action="delete_fact",
                    fact_id=fact.id,
                    conversation_id=conversation_id,
                )
                return MemoryCorrectionResult(
                    handled=True,
                    reply=f"Dạ em đã xóa fact #{fact.id}: {fact.subject} - {fact.content}",
                    decision=gate,
                )
            reply = "Dạ fact này vừa không còn tồn tại trong memory, nên em chưa xóa thêm gì cả anh."
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        if pending.decision == MEMORY_CORRECT_MEMORY:
            replacement = pending.replacement.strip()
            if not replacement:
                reply = f"Dạ anh gửi nội dung mới cho fact #{fact.id} giúp em nhé, hiện em chưa sửa gì cả."
                self._trace_memory_correction_clarify(
                    trace_logger,
                    trace_id,
                    gate,
                    "pending_missing_replacement",
                    fact_ids=[fact.id],
                )
                return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)
            self._clear_pending_correction(conversation_id)
            meta = dict(fact.meta)
            meta["corrected_by"] = "chat"
            meta["correction_trace_id"] = trace_id
            meta["previous_content"] = fact.content
            updated = self.store.update_fact(fact.id, fact.subject, replacement, meta=meta)
            if updated:
                self._trace_memory_correction_applied(
                    trace_logger,
                    trace_id,
                    gate,
                    action="update_fact",
                    fact_id=fact.id,
                    conversation_id=conversation_id,
                )
                return MemoryCorrectionResult(
                    handled=True,
                    reply=f"Dạ em đã sửa fact #{fact.id} thành: {replacement}",
                    decision=gate,
                )

        return None

    def _is_pending_fact_choice_prompt(self, conversation_id: str, prompt: str) -> bool:
        """Nhận diện reply chỉ chọn ID trong workflow đang chờ, không tự đổi intent."""
        if self._get_pending_correction(conversation_id) is None:
            return False
        if self._extract_fact_id(prompt) is None:
            return False

        normalized = self._normalize_pending_choice_text(prompt)
        normalized = re.sub(r"@\w+", " ", normalized)
        normalized = re.sub(r"(?:\[fact:|fact\s*(?:#|:)?\s*)\d+\]?", " ", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"[#:\[\]().,!?\-_/\\]+", " ", normalized)
        words = [word for word in normalized.split() if not word.isdigit()]
        if not words:
            return True

        explicit_action_words = {
            "sua",
            "fix",
            "correct",
            "update",
            "cap",
            "nhat",
            "thay",
            "thanh",
            "doi",
            "xoa",
            "xoa",
            "delete",
            "remove",
            "forget",
            "quen",
        }
        if any(word in explicit_action_words for word in words):
            return False

        polite_choice_words = {
            "a",
            "anh",
            "chon",
            "cai",
            "da",
            "day",
            "di",
            "do",
            "em",
            "giup",
            "nha",
            "nhe",
            "nho",
            "ok",
            "oke",
            "thi",
            "um",
            "uh",
            "vang",
            "voi",
        }
        return all(word in polite_choice_words for word in words)

    def _remember_pending_correction(self, conversation_id: str, gate: dict[str, object], fact_ids: list[int]) -> None:
        """Ghi metadata phụ để model/callback biết danh sách fact ID hợp lệ ở lượt sau."""
        with self._pending_corrections_lock:
            self._pending_corrections[conversation_id] = PendingMemoryCorrection(
                decision=str(gate["decision"]),
                query=str(gate["query"]),
                replacement=str(gate["replacement"]),
                fact_ids=fact_ids,
                created_at=time.time(),
            )

    def _get_pending_correction(self, conversation_id: str) -> PendingMemoryCorrection | None:
        with self._pending_corrections_lock:
            pending = self._pending_corrections.get(conversation_id)
            if pending is None:
                return None
            if time.time() - pending.created_at > 300:
                self._pending_corrections.pop(conversation_id, None)
                return None
            return pending

    def _clear_pending_correction(self, conversation_id: str) -> None:
        with self._pending_corrections_lock:
            self._pending_corrections.pop(conversation_id, None)

    @staticmethod
    def _extract_fact_id(prompt: str) -> int | None:
        match = re.search(r"(?:\[fact:|fact\s*(?:#|:)?\s*)(\d+)\]?", prompt, flags=re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _normalize_pending_choice_text(value: str) -> str:
        """Bỏ dấu/case để nhận diện các hạt lịch sự quanh lựa chọn fact ID."""
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
        return normalized.replace("đ", "d")

    @staticmethod
    def _build_ambiguous_fact_reply(facts) -> str:
        lines = [
            "Dạ em tìm thấy nhiều fact có thể khớp, nên em chưa sửa/xóa gì để tránh nhầm. Anh chỉ rõ ID giúp em nhé:"
        ]
        for fact in facts[:5]:
            lines.append(f"- fact #{fact.id}: {fact.subject} - {fact.content}")
        return "\n".join(lines)

    @staticmethod
    def _build_pending_fact_mismatch_reply(fact_ids: list[int]) -> str:
        offered = ", ".join(f"#{fact_id}" for fact_id in fact_ids)
        return f"Dạ ID đó không nằm trong danh sách em vừa đưa. Anh chọn một trong các fact này giúp em nhé: {offered}."

    @staticmethod
    def _truncate_decision_text(value: str, limit: int = 700) -> str:
        text = value.strip()
        if len(text) <= limit:
            return text
        return text[: limit - 20].rstrip() + "\n...[truncated]"

    def _trace_memory_correction_applied(
        self,
        trace_logger,
        trace_id: str,
        gate: dict[str, object],
        *,
        action: str,
        fact_id: int,
        conversation_id: str,
    ) -> None:
        data = {
            **self._gate_event_data(gate),
            "action": action,
            "fact_id": fact_id,
            "conversation_id": conversation_id,
        }
        trace_logger.event(trace_id, "memory_correction_applied", data)
        log_memory_correction_applied(data)

    def _trace_memory_correction_clarify(
        self,
        trace_logger,
        trace_id: str,
        gate: dict[str, object],
        reason: str,
        *,
        fact_ids: list[int] | None = None,
    ) -> None:
        data = {**self._gate_event_data(gate), "clarify_reason": reason}
        if fact_ids:
            data["fact_ids"] = fact_ids
        trace_logger.event(trace_id, "memory_correction_clarify", data)
        log_memory_correction_clarify(data)

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
            gate_fact_mode=str(gate["fact_mode"]),
            gate_episode_mode=str(gate["episode_mode"]),
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


def log_memory_correction_decision(gate_state: dict[str, object]) -> None:
    """Ghi log intent sửa/xóa memory để dashboard thấy model đã quyết định gì."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_decision",
            (
                "Memory correction: "
                f"decision={gate_state.get('decision') or 'disabled'} "
                f"query={gate_state.get('query') or '-'} "
                f"target={gate_state.get('target_type') or '-'}"
            ),
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def log_memory_correction_error(gate_state: dict[str, object]) -> None:
    """Correction gate lỗi thì fail-closed: không mutate DB, chỉ log warning."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_error",
            f"Memory correction gate loi, fail-closed: {gate_state.get('error')}",
            level="warning",
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def log_memory_correction_applied(data: dict[str, object]) -> None:
    """Log thao tác update/delete fact đã thực sự được áp dụng."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_applied",
            f"Memory correction applied: action={data.get('action')} fact_id={data.get('fact_id')}",
            data=data,
        )
    except Exception:
        pass


def log_memory_correction_clarify(data: dict[str, object]) -> None:
    """Log trường hợp gate hiểu intent nhưng runtime cần hỏi lại để tránh nhầm."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_clarify",
            f"Memory correction needs clarification: {data.get('clarify_reason')}",
            data=data,
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
]
