"""Kiểu dữ liệu, config helper và formatter cho memory context.

Quyết định lấy gì nằm ở `MemoryRuntime` và Decision Model. File này giữ
`RetrievedMemory`, các flag cấu hình, helper log gate và formatter biến
facts/episodes thành đoạn context phụ trợ cho Deep. Nó chưa làm embedding,
rerank hay graph reasoning; retrieval v1 vẫn là FTS/LIKE text search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from niko.config import env_flag, env_value
from niko.memory.store import Episode, Fact, MemoryStore


DEFAULT_MEMORY_TOP_K = 4


@dataclass(frozen=True)
class RetrievedMemory:
    """Kết quả retrieval gồm cả text prompt và record raw để trace."""

    text: str
    facts: list[Fact]
    episodes: list[Episode]
    enabled: bool
    gate_enabled: bool = False
    gate_decision: str = ""
    gate_query: str = ""
    gate_reason: str = ""
    gate_fact_mode: str = ""
    gate_episode_mode: str = ""
    gate_confidence: float | None = None
    gate_label: str = ""
    gate_probabilities: dict[str, float] | None = None
    gate_model: str = ""
    gate_error: str = ""

    def to_meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "enabled": self.enabled,
            "fact_ids": [fact.id for fact in self.facts],
            "episode_ids": [episode.id for episode in self.episodes],
            "fact_count": len(self.facts),
            "episode_count": len(self.episodes),
        }
        if self.gate_enabled:
            meta["gate_enabled"] = True
            if self.gate_decision:
                meta["gate_decision"] = self.gate_decision
            if self.gate_query:
                meta["gate_query"] = self.gate_query
            if self.gate_reason:
                meta["gate_reason"] = self.gate_reason
            if self.gate_fact_mode:
                meta["gate_fact_mode"] = self.gate_fact_mode
            if self.gate_episode_mode:
                meta["gate_episode_mode"] = self.gate_episode_mode
            if self.gate_confidence is not None:
                meta["gate_confidence"] = self.gate_confidence
            if self.gate_label:
                meta["gate_label"] = self.gate_label
            if self.gate_probabilities:
                meta["gate_probabilities"] = self.gate_probabilities
            if self.gate_model:
                meta["gate_model"] = self.gate_model
            if self.gate_error:
                meta["gate_error"] = self.gate_error
        return meta


def memory_enabled() -> bool:
    return env_flag("NIKO_MEMORY_ENABLED", "1")


def memory_retrieval_enabled() -> bool:
    return memory_enabled() and env_flag("NIKO_MEMORY_RETRIEVAL_ENABLED", "1")


def memory_gate_enabled() -> bool:
    """Gate dùng Nimble để quyết định có retrieve memory hay không; default-off."""
    return memory_retrieval_enabled() and env_flag("NIKO_MEMORY_GATE_ENABLED", "0")


def memory_write_gate_enabled() -> bool:
    """Gate dùng Nimble để quyết định Deep episode nào đáng ghi dài hạn; default-off."""
    return memory_write_enabled() and env_flag("NIKO_MEMORY_WRITE_GATE_ENABLED", "0")


def memory_top_k() -> int:
    raw_value = env_value("NIKO_MEMORY_TOP_K", str(DEFAULT_MEMORY_TOP_K)).strip()
    try:
        return max(1, min(20, int(raw_value)))
    except ValueError:
        return DEFAULT_MEMORY_TOP_K


def evaluate_memory_gate(prompt: str, gateway_message=None) -> dict[str, Any]:
    """Chạy retrieval gate nếu bật; lỗi thì fail-open bằng raw prompt."""
    from niko.memory.runtime import default_memory_runtime

    return default_memory_runtime().evaluate_retrieval_gate(prompt, gateway_message)


def log_memory_gate_decision(gate_state: dict[str, Any]) -> None:
    """Ghi log ngắn cho tab Bots; lỗi log không được ảnh hưởng turn."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_gate_decision",
            (
                "Memory gate: "
                f"decision={gate_state.get('decision') or 'disabled'} "
                f"query={gate_state.get('query') or '-'} "
                f"fact_mode={gate_state.get('fact_mode') or '-'} "
                f"episode_mode={gate_state.get('episode_mode') or '-'}"
            ),
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def log_memory_gate_error(gate_state: dict[str, Any]) -> None:
    """Gate lỗi thì chỉ log, còn retrieval fail-open ở caller."""
    try:
        from niko.harness.runtime_log import default_runtime_logger

        default_runtime_logger().event(
            "memory",
            "memory_gate_error",
            f"Memory gate loi, fail-open retrieval: {gate_state.get('error')}",
            level="warning",
            data={key: value for key, value in gate_state.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def retrieve_memory_context(
    prompt: str,
    gateway_message=None,
    store: MemoryStore | None = None,
) -> RetrievedMemory:
    """Truy xuất facts/episodes liên quan cho một prompt."""
    from niko.memory.runtime import MemoryRuntime, default_memory_runtime

    runtime = MemoryRuntime(store=store) if store is not None else default_memory_runtime()
    return runtime.retrieve_for_deep(prompt, gateway_message=gateway_message)


def build_memory_context(prompt: str, gateway_message=None, store: MemoryStore | None = None) -> str:
    return retrieve_memory_context(prompt, gateway_message=gateway_message, store=store).text


def format_memory_context(
    facts: list[Fact],
    episodes: list[Episode],
    gateway_message=None,
) -> str:
    """Format memory thành đoạn prompt phụ, luôn nhắc Deep ưu tiên message mới."""
    if not facts and not episodes:
        return ""

    lines = [
        "Ngu canh bo nho lien quan cua Niko:",
        "- Day la context phu tro, khong thay the tin nhan moi nhat cua nguoi dung.",
        "- Neu bo nho mau thuan voi tin nhan moi, uu tien tin nhan moi va hoi lai khi can.",
    ]
    if gateway_message is not None:
        lines.append(f"- conversation_id: {gateway_message.chat_id or gateway_message.user.key}")
        lines.append(f"- user_key: {gateway_message.user.key}")

    if facts:
        lines.append("")
        lines.append("Semantic memory / facts:")
        for fact in facts:
            lines.append(f"- [fact:{fact.id}] {fact.subject}: {fact.content}")

    if episodes:
        lines.append("")
        lines.append("Episodic memory / events:")
        for episode in episodes:
            lines.append(f"- [episode:{episode.id} @ {episode.happened_at}] {episode.summary}")

    return "\n".join(lines)


def compact_episode_summary(prompt: str, answer: str, followups: list[str] | None = None, limit: int = 900) -> str:
    """Tóm tắt episode baseline sau Deep job; chưa phải event model giàu ngữ nghĩa."""
    pieces = [
        f"User asked: {prompt.strip()}",
        f"Niko answered: {answer.strip()}",
    ]
    if followups:
        pieces.append("Followups while waiting: " + " | ".join(item.strip() for item in followups if item.strip()))
    summary = "\n".join(piece for piece in pieces if piece.strip())
    if len(summary) <= limit:
        return summary
    return summary[: limit - 20].rstrip() + "\n...[truncated]"


def memory_write_enabled() -> bool:
    """Memory write có thể tắt độc lập retrieval để test/demo sạch dữ liệu."""
    return memory_enabled() and env_flag("NIKO_MEMORY_WRITE_ENABLED", "1")
