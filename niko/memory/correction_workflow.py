"""Workflow sửa/xóa chat memory có kiểm soát.

Module này tách Phase 5 V1 ra khỏi `MemoryRuntime`: Decision Model chỉ nhận diện
intent và trích query/replacement, còn workflow Python giữ pending state, search
fact, validate ID, update/delete SQLite và ghi trace/runtime log. Pending hiện
vẫn là RAM state tạm thời; interface này giúp sau này đổi sang Loop/tool workflow
có state bền mà không làm phình `MemoryRuntime`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
import time
import unicodedata
from typing import Callable

from bots.decision_model.memory import (
    MEMORY_CORRECT_MEMORY,
    MEMORY_CORRECTION_NONE,
    MEMORY_FORGET_MEMORY,
    MEMORY_TARGET_EPISODE,
    MEMORY_TARGET_FACT,
    MEMORY_TARGET_UNKNOWN,
    MemoryCorrectionDecision,
    decide_memory_correction_intent,
)
from niko.memory.context import memory_correction_detection_enabled
from niko.memory.store import MemoryStore, default_memory_store
from niko.memory.working_memory import recent_chat_window


MemoryCorrectionDecider = Callable[..., MemoryCorrectionDecision]
MemoryStoreProvider = Callable[[], MemoryStore]


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


class MemoryCorrectionWorkflow:
    """Điều phối một workflow sửa/xóa fact qua chat, có trace và guardrail."""

    def __init__(
        self,
        store_provider: MemoryStoreProvider | None = None,
        correction_decider: MemoryCorrectionDecider | None = None,
    ) -> None:
        self._store_provider = store_provider
        self._correction_decider = correction_decider
        self._pending_corrections: dict[str, PendingMemoryCorrection] = {}
        self._pending_corrections_lock = threading.RLock()

    @property
    def store(self) -> MemoryStore:
        """Luôn lấy store qua provider để dashboard/runtime config mới vẫn có hiệu lực."""
        return self._store_provider() if self._store_provider is not None else default_memory_store()

    def handle(
        self,
        conversation_id: str,
        prompt: str,
        gateway_message,
        trace_id: str,
        trace_logger,
    ) -> MemoryCorrectionResult:
        """Xử lý yêu cầu sửa/xóa fact qua chat nếu correction gate nhận diện rõ."""
        decision_context = self.build_decision_context(conversation_id, prompt)
        gate = self.evaluate_intent(prompt, gateway_message, decision_context)
        if not gate["enabled"]:
            return MemoryCorrectionResult(handled=False, decision=gate)

        trace_logger.event(trace_id, "memory_correction_decision", gate_event_data(gate))
        if self._is_pending_fact_choice_prompt(conversation_id, prompt):
            pending_result = self._handle_pending(conversation_id, prompt, trace_id, trace_logger, gate)
            if pending_result is not None:
                return pending_result

        if gate["error"] or gate["decision"] == MEMORY_CORRECTION_NONE:
            pending_result = self._handle_pending(conversation_id, prompt, trace_id, trace_logger, gate)
            if pending_result is not None:
                return pending_result
            return MemoryCorrectionResult(handled=False, decision=gate)

        if gate["target_type"] == MEMORY_TARGET_EPISODE:
            reply = (
                "Dạ phần episode hiện tại em chỉ cho xem, chưa cho sửa hoặc xóa qua chat để tránh mất "
                "ngữ cảnh lịch sử. Nếu anh muốn, em sẽ xử lý phần này ở phase dashboard/ops sau."
            )
            self._trace_clarify(trace_logger, trace_id, gate, "episode_read_only")
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        facts = self._find_facts(prompt, str(gate["query"]))
        if not facts:
            reply = "Dạ em chưa tìm thấy fact nào khớp rõ với yêu cầu này, nên em chưa sửa/xóa gì cả anh."
            self._trace_clarify(trace_logger, trace_id, gate, "no_fact_match")
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        if len(facts) > 1:
            self._remember_pending(conversation_id, gate, [fact.id for fact in facts])
            reply = self._build_ambiguous_fact_reply(facts)
            self._trace_clarify(
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
                self._trace_applied(
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
            self._trace_clarify(trace_logger, trace_id, gate, "fact_delete_missed", fact_ids=[fact.id])
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        if gate["decision"] == MEMORY_CORRECT_MEMORY:
            replacement = str(gate["replacement"]).strip()
            if not replacement:
                reply = "Dạ anh nói rõ nội dung mới cần thay cho fact đó giúp em nhé, hiện em chưa sửa gì cả."
                self._trace_clarify(trace_logger, trace_id, gate, "missing_replacement", fact_ids=[fact.id])
                return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

            updated = self._update_fact_from_chat(fact, replacement, trace_id)
            if updated:
                self._trace_applied(
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
            self._trace_clarify(trace_logger, trace_id, gate, "fact_update_missed", fact_ids=[fact.id])
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        return MemoryCorrectionResult(handled=False, decision=gate)

    def evaluate_intent(
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
            decision = self._decide(prompt, gateway_message, decision_context)
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

    def build_decision_context(self, conversation_id: str, prompt: str) -> dict[str, object]:
        """Gửi vài turn chat gần nhất là ngữ cảnh chính; pending chỉ là metadata phụ."""
        pending = self._get_pending(conversation_id)
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

    def _decide(
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

    def _recent_turns_for_decision(self, conversation_id: str, current_prompt: str, limit: int = 6) -> list[dict[str, str]]:
        """Lấy vài chat_log gần nhất, bỏ chính incoming prompt hiện tại nếu đã được ghi."""
        return recent_chat_window(
            self.store,
            conversation_id,
            current_prompt,
            limit=limit,
            char_budget=4200,
            per_turn_limit=700,
        )

    def _find_facts(self, prompt: str, query: str):
        """Tìm fact bằng ID rõ ràng trước, sau đó mới dùng search text."""
        fact_id = extract_fact_id(prompt)
        if fact_id is not None:
            return [fact for fact in self.store.list_facts(limit=200) if fact.id == fact_id]
        search_query = query or prompt
        return self.store.search_facts(search_query, top_k=5)

    def _handle_pending(
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
        fact_id = extract_fact_id(prompt)
        if fact_id is None:
            return None
        pending = self._get_pending(conversation_id)
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
        trace_logger.event(trace_id, "memory_correction_context_fallback", gate_event_data(gate))

        if fact_id not in pending.fact_ids:
            reply = self._build_pending_fact_mismatch_reply(pending.fact_ids)
            self._trace_clarify(
                trace_logger,
                trace_id,
                gate,
                "pending_fact_id_not_offered",
                fact_ids=pending.fact_ids,
            )
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        facts = [fact for fact in self.store.list_facts(limit=200) if fact.id == fact_id]
        if not facts:
            self._clear_pending(conversation_id)
            reply = f"Dạ fact #{fact_id} hiện không còn tồn tại trong memory, nên em chưa sửa/xóa gì cả anh."
            self._trace_clarify(
                trace_logger,
                trace_id,
                gate,
                "pending_fact_missing",
                fact_ids=[fact_id],
            )
            return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)

        fact = facts[0]
        if pending.decision == MEMORY_FORGET_MEMORY:
            self._clear_pending(conversation_id)
            deleted = self.store.delete_fact(fact.id)
            if deleted:
                self._trace_applied(
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
                self._trace_clarify(
                    trace_logger,
                    trace_id,
                    gate,
                    "pending_missing_replacement",
                    fact_ids=[fact.id],
                )
                return MemoryCorrectionResult(handled=True, reply=reply, decision=gate)
            self._clear_pending(conversation_id)
            if self._update_fact_from_chat(fact, replacement, trace_id):
                self._trace_applied(
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

    def _update_fact_from_chat(self, fact, replacement: str, trace_id: str) -> bool:
        meta = dict(fact.meta)
        meta["corrected_by"] = "chat"
        meta["correction_trace_id"] = trace_id
        meta["previous_content"] = fact.content
        return self.store.update_fact(fact.id, fact.subject, replacement, meta=meta)

    def _is_pending_fact_choice_prompt(self, conversation_id: str, prompt: str) -> bool:
        """Nhận diện reply chỉ chọn ID trong workflow đang chờ, không tự đổi intent."""
        if self._get_pending(conversation_id) is None:
            return False
        if extract_fact_id(prompt) is None:
            return False

        normalized = normalize_pending_choice_text(prompt)
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

    def _remember_pending(self, conversation_id: str, gate: dict[str, object], fact_ids: list[int]) -> None:
        """Ghi metadata phụ để model/callback biết danh sách fact ID hợp lệ ở lượt sau."""
        with self._pending_corrections_lock:
            self._pending_corrections[conversation_id] = PendingMemoryCorrection(
                decision=str(gate["decision"]),
                query=str(gate["query"]),
                replacement=str(gate["replacement"]),
                fact_ids=fact_ids,
                created_at=time.time(),
            )

    def _get_pending(self, conversation_id: str) -> PendingMemoryCorrection | None:
        with self._pending_corrections_lock:
            pending = self._pending_corrections.get(conversation_id)
            if pending is None:
                return None
            if time.time() - pending.created_at > 300:
                self._pending_corrections.pop(conversation_id, None)
                return None
            return pending

    def _clear_pending(self, conversation_id: str) -> None:
        with self._pending_corrections_lock:
            self._pending_corrections.pop(conversation_id, None)

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

    def _trace_applied(
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
            **gate_event_data(gate),
            "action": action,
            "fact_id": fact_id,
            "conversation_id": conversation_id,
        }
        trace_logger.event(trace_id, "memory_correction_applied", data)
        log_memory_correction_applied(data)

    def _trace_clarify(
        self,
        trace_logger,
        trace_id: str,
        gate: dict[str, object],
        reason: str,
        *,
        fact_ids: list[int] | None = None,
    ) -> None:
        data = {**gate_event_data(gate), "clarify_reason": reason}
        if fact_ids:
            data["fact_ids"] = fact_ids
        trace_logger.event(trace_id, "memory_correction_clarify", data)
        log_memory_correction_clarify(data)


def extract_fact_id(prompt: str) -> int | None:
    """Tách `fact #123` hoặc `[fact:123]` khỏi prompt follow-up."""
    match = re.search(r"(?:\[fact:|fact\s*(?:#|:)?\s*)(\d+)\]?", prompt, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def normalize_pending_choice_text(value: str) -> str:
    """Bỏ dấu/case để nhận diện các hạt lịch sự quanh lựa chọn fact ID."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    return normalized.replace("đ", "d")


def truncate_decision_text(value: str, limit: int = 700) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


def gate_event_data(gate: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in gate.items() if value not in ("", None, {})}


def log_memory_correction_decision(gate_state: dict[str, object]) -> None:
    """Ghi log correction gate cho tab Bots; lỗi log không được ảnh hưởng turn."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_decision",
            (
                "Memory correction gate: "
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
            f"Memory correction gate loi, bo qua mutate: {gate_state.get('error')}",
            level="warning",
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def log_memory_correction_applied(data: dict[str, object]) -> None:
    """Ghi log khi workflow đã mutate fact thành công."""
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
    """Ghi log khi workflow cần hỏi lại hoặc từ chối mutate."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_correction_clarify",
            f"Memory correction clarify: reason={data.get('clarify_reason')}",
            data=data,
        )
    except Exception:
        pass


__all__ = [
    "MemoryCorrectionDecider",
    "MemoryCorrectionResult",
    "MemoryCorrectionWorkflow",
    "PendingMemoryCorrection",
    "extract_fact_id",
    "gate_event_data",
    "normalize_pending_choice_text",
    "truncate_decision_text",
]
