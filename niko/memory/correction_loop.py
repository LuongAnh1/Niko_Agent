"""Bridge correction chạy qua Loop core V0.

Module này là đường thử nghiệm default-off giữa correction gate hiện tại và
LoopRuntime. Nó xử lý prompt sửa/xóa fact trực tiếp bằng memory fact tools, còn
pending follow-up kiểu `fact #...` và state bền vẫn do `MemoryCorrectionWorkflow`
quản lý qua SQLite. Khi loop lỗi, caller fallback về workflow V1 để giữ hành vi
chat đã live-test ổn định.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from bots.decision_model.memory import (
    MEMORY_CORRECT_MEMORY,
    MEMORY_FORGET_MEMORY,
    extract_memory_correction_query_from_prompt,
    normalize_correction_prompt_text,
)
from niko.loop import LoopDecision, LoopResult, LoopRuntime, ToolContext, ToolRegistry, TraceLoopObserver
from niko.memory.loop_tools import build_memory_fact_tools
from niko.memory.store import MemoryStore, default_memory_store


@dataclass(frozen=True)
class MemoryCorrectionLoopOutcome:
    """Kết quả bridge từ LoopRuntime về MemoryCorrectionWorkflow V1."""

    handled: bool
    reply: str = ""
    reason: str = ""
    action: str = ""
    fact_id: int | None = None
    fact_ids: list[int] = field(default_factory=list)
    loop_result: LoopResult | None = None
    error: str = ""


class MemoryCorrectionLoopWorkflow:
    """Chạy direct correction prompt bằng Loop + fact tools khi caller bật cờ."""

    def __init__(self, store_provider=None, max_iterations: int = 3) -> None:
        self._store_provider = store_provider or default_memory_store
        self.max_iterations = max_iterations

    @property
    def store(self) -> MemoryStore:
        return self._store_provider()

    def handle(
        self,
        *,
        prompt: str,
        gate: dict[str, object],
        conversation_id: str,
        trace_id: str,
        trace_logger,
    ) -> MemoryCorrectionLoopOutcome:
        """Chạy một turn direct correction; pending bền được xử lý ở facade V1."""
        controller = MemoryCorrectionLoopController(prompt=prompt, gate=gate)
        registry = ToolRegistry(build_memory_fact_tools(store_provider=lambda: self.store))
        observer = TraceLoopObserver(trace_logger, trace_id=trace_id) if trace_logger is not None else None
        runtime = LoopRuntime(controller, registry, max_iterations=self.max_iterations, observer=observer)
        loop_result = runtime.run(
            prompt,
            ToolContext(
                trace_id=trace_id,
                conversation_id=conversation_id,
                state={"memory_correction_gate": gate},
            ),
        )
        if loop_result.error:
            return MemoryCorrectionLoopOutcome(False, loop_result=loop_result, error=loop_result.error)
        return controller.outcome(loop_result)


class MemoryCorrectionLoopController:
    """Controller deterministic V0 để kiểm thử contract tool trước native tool-use."""

    def __init__(self, *, prompt: str, gate: dict[str, object]) -> None:
        self.prompt = prompt
        self.gate = gate
        self.final_reason = ""
        self.final_fact_ids: list[int] = []
        self.action = ""
        self.fact_id: int | None = None
        self.target_fact: dict[str, Any] | None = None

    def __call__(
        self,
        prompt: str,
        context: ToolContext,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LoopDecision:
        if not history:
            query = _effective_query(prompt, self.gate)
            return LoopDecision.tool("search_facts", {"query": query}, reason="search_target_facts")

        last_tool = _last_tool_result(history)
        if last_tool is None:
            self.final_reason = "invalid_loop_history"
            return LoopDecision.final("Dạ loop sửa/xóa memory chưa có kết quả tool hợp lệ, nên em chưa sửa/xóa gì cả.")

        tool_name = str(last_tool.get("tool_name", ""))
        if tool_name == "search_facts":
            return self._after_search(last_tool)
        if tool_name in {"delete_fact", "update_fact"}:
            return self._after_mutation(tool_name, last_tool)

        self.final_reason = "unknown_tool_result"
        return LoopDecision.final("Dạ em chưa xử lý được kết quả tool này, nên em chưa sửa/xóa gì cả.")

    def outcome(self, loop_result: LoopResult) -> MemoryCorrectionLoopOutcome:
        return MemoryCorrectionLoopOutcome(
            handled=True,
            reply=loop_result.reply,
            reason=self.final_reason,
            action=self.action,
            fact_id=self.fact_id,
            fact_ids=self.final_fact_ids,
            loop_result=loop_result,
        )

    def _after_search(self, tool_result: dict[str, Any]) -> LoopDecision:
        if not bool(tool_result.get("ok")):
            self.final_reason = str(tool_result.get("error") or "search_failed")
            return LoopDecision.final("Dạ em chưa tìm được fact phù hợp, nên em chưa sửa/xóa gì cả.")

        facts = _facts_from_tool_result(tool_result)
        if not facts:
            self.final_reason = "no_fact_match"
            return LoopDecision.final("Dạ em chưa tìm thấy fact nào khớp rõ với yêu cầu này, nên em chưa sửa/xóa gì cả anh.")
        facts = _filter_relevant_facts(facts, _effective_query(self.prompt, self.gate))
        if not facts:
            self.final_reason = "no_fact_match"
            return LoopDecision.final("Dạ em chưa tìm thấy fact nào khớp rõ với yêu cầu này, nên em chưa sửa/xóa gì cả anh.")
        if len(facts) > 1:
            unique_fact = _select_unique_fact(facts, _effective_query(self.prompt, self.gate))
            if unique_fact is not None:
                facts = [unique_fact]
        if len(facts) > 1:
            self.final_reason = "ambiguous_fact_match"
            self.final_fact_ids = [int(fact["id"]) for fact in facts if "id" in fact]
            return LoopDecision.final(_ambiguous_fact_reply(facts))

        fact = facts[0]
        self.target_fact = fact
        fact_id = int(fact["id"])
        decision = str(self.gate.get("decision") or "")
        if decision == MEMORY_FORGET_MEMORY:
            return LoopDecision.tool("delete_fact", {"fact_id": fact_id}, reason="delete_unique_fact")
        if decision == MEMORY_CORRECT_MEMORY:
            replacement = str(self.gate.get("replacement") or "").strip()
            if not replacement:
                self.final_reason = "missing_replacement"
                self.final_fact_ids = [fact_id]
                return LoopDecision.final(
                    "Dạ anh nói rõ nội dung mới cần thay cho fact đó giúp em nhé, hiện em chưa sửa gì cả."
                )
            return LoopDecision.tool(
                "update_fact",
                {
                    "fact_id": fact_id,
                    "subject": str(fact.get("subject") or "Chat memory"),
                    "content": replacement,
                },
                reason="update_unique_fact",
            )

        self.final_reason = "unsupported_correction_decision"
        return LoopDecision.final("Dạ yêu cầu này chưa thuộc nhóm sửa/xóa fact mà loop hỗ trợ, nên em chưa thay đổi gì cả.")

    def _after_mutation(self, tool_name: str, tool_result: dict[str, Any]) -> LoopDecision:
        data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
        fact_id = int(data.get("fact_id") or 0)
        if not bool(tool_result.get("ok")):
            self.final_reason = str(tool_result.get("error") or "mutation_failed")
            self.final_fact_ids = [fact_id] if fact_id else []
            return LoopDecision.final("Dạ fact này vừa không còn tồn tại trong memory, nên em chưa sửa/xóa gì cả anh.")

        self.action = tool_name
        self.fact_id = fact_id
        self.final_reason = "applied"
        if tool_name == "delete_fact":
            fact = self.target_fact or {}
            return LoopDecision.final(
                f"Dạ em đã xóa fact #{fact_id}: {fact.get('subject', 'Chat memory')} - {fact.get('content', '')}"
            )
        replacement = str(self.gate.get("replacement") or data.get("content") or "").strip()
        return LoopDecision.final(f"Dạ em đã sửa fact #{fact_id} thành: {replacement}")


def _last_tool_result(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(history):
        if item.get("type") == "tool_result":
            return item
    return None


def _facts_from_tool_result(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    data = tool_result.get("data")
    if not isinstance(data, dict):
        return []
    facts = data.get("facts")
    if not isinstance(facts, list):
        return []
    return [fact for fact in facts if isinstance(fact, dict) and fact.get("id")]


def _effective_query(prompt: str, gate: dict[str, object]) -> str:
    query = str(gate.get("query") or "").strip()
    if query:
        return query
    return extract_memory_correction_query_from_prompt(prompt) or prompt


def _select_unique_fact(facts: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Chọn fact vượt trội khi query đủ cụ thể; query ngắn vẫn hỏi lại."""
    words = _target_words(query)
    if len(words) < 2:
        return None
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for fact in facts:
        score, matched = _fact_match_score(fact, words, query)
        if matched:
            scored.append((score, matched, fact))
    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], -item[1], int(item[2].get("id") or 0)))
    top_score, top_matched, top_fact = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if top_matched == len(words) and top_score > second_score:
        return top_fact
    if top_matched >= max(2, len(words) - 1) and top_score >= second_score + 2:
        return top_fact
    return None


def _filter_relevant_facts(facts: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Loại match quá yếu trước khi hỏi user chọn ID."""
    words = _target_words(query)
    if len(words) < 2:
        return facts
    required_matches = max(2, (len(words) + 1) // 2)
    relevant: list[dict[str, Any]] = []
    for fact in facts:
        score, matched = _fact_match_score(fact, words, query)
        if matched >= required_matches or score >= len(words) + 1:
            relevant.append(fact)
    return relevant


def _target_words(query: str) -> list[str]:
    stopwords = {
        "anh",
        "em",
        "niko",
        "fact",
        "facts",
        "memory",
        "mem",
        "quen",
        "xoa",
        "delete",
        "remove",
        "forget",
        "sua",
        "thich",
        "mau",
        "update",
        "fix",
        "correct",
        "nhe",
        "nha",
        "voi",
    }
    words = []
    seen: set[str] = set()
    for word in re.findall(r"[^\W_]+", normalize_correction_prompt_text(query), flags=re.UNICODE):
        if len(word) < 2 or word in stopwords or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def _fact_match_score(fact: dict[str, Any], words: list[str], query: str) -> tuple[int, int]:
    haystack = normalize_correction_prompt_text(f"{fact.get('subject', '')} {fact.get('content', '')}")
    haystack_words = set(re.findall(r"[^\W_]+", haystack, flags=re.UNICODE))
    matched = sum(1 for word in words if word in haystack_words)
    score = matched
    normalized_query = normalize_correction_prompt_text(query)
    if normalized_query and normalized_query in haystack:
        score += len(words) + 1
    return score, matched


def _ambiguous_fact_reply(facts: list[dict[str, Any]]) -> str:
    lines = [
        "Dạ em tìm thấy nhiều fact có thể khớp, nên em chưa sửa/xóa gì để tránh nhầm. Anh chỉ rõ ID giúp em nhé:"
    ]
    for fact in facts[:5]:
        lines.append(f"- fact #{fact.get('id')}: {fact.get('subject')} - {fact.get('content')}")
    return "\n".join(lines)


__all__ = ["MemoryCorrectionLoopOutcome", "MemoryCorrectionLoopWorkflow"]
