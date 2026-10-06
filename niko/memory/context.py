"""Memory retrieval context cho Deep agent.

Graph chỉ cần một đoạn text phụ trợ để nhét vào prompt Deep. File này quyết
định lấy facts/episodes nào từ SQLite baseline và format chúng thành context.
Nó chưa làm embedding, rerank hay graph reasoning; đây là tầng text retrieval
đủ rõ để demo memory pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import unicodedata
from typing import Any

from niko.config import env_flag, env_value
from niko.memory.store import Episode, Fact, MemoryStore, default_memory_store


DEFAULT_MEMORY_TOP_K = 4
FACT_INVENTORY_KEYWORDS = (
    "fact nao",
    "facts nao",
    "fact gi",
    "facts gi",
    "su that nao",
    "su that gi",
    "dang luu fact",
    "dang luu facts",
    "dang luu su that",
    "luu fact",
    "luu facts",
    "luu su that",
    "semantic nao",
    "semantic memory nao",
    "bo nho semantic",
)
EPISODE_INVENTORY_KEYWORDS = (
    "episode nao",
    "episodes nao",
    "su kien nao",
    "nhat ky nao",
    "dang luu episode",
    "dang luu su kien",
    "episodic nao",
    "episodic memory nao",
    "bo nho episodic",
)
FACT_INVENTORY_FILTER_STOPWORDS = {
    "bo",
    "co",
    "dang",
    "em",
    "fact",
    "facts",
    "gi",
    "khong",
    "luu",
    "memory",
    "nao",
    "semantic",
    "su",
    "that",
    "ve",
}


@dataclass(frozen=True)
class RetrievedMemory:
    """Kết quả retrieval gồm cả text prompt và record raw để trace."""

    text: str
    facts: list[Fact]
    episodes: list[Episode]
    enabled: bool

    def to_meta(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "fact_ids": [fact.id for fact in self.facts],
            "episode_ids": [episode.id for episode in self.episodes],
            "fact_count": len(self.facts),
            "episode_count": len(self.episodes),
        }


def memory_enabled() -> bool:
    return env_flag("NIKO_MEMORY_ENABLED", "1")


def memory_retrieval_enabled() -> bool:
    return memory_enabled() and env_flag("NIKO_MEMORY_RETRIEVAL_ENABLED", "1")


def memory_top_k() -> int:
    raw_value = env_value("NIKO_MEMORY_TOP_K", str(DEFAULT_MEMORY_TOP_K)).strip()
    try:
        return max(1, min(20, int(raw_value)))
    except ValueError:
        return DEFAULT_MEMORY_TOP_K


def retrieve_memory_context(
    prompt: str,
    gateway_message=None,
    store: MemoryStore | None = None,
) -> RetrievedMemory:
    """Truy xuất facts/episodes liên quan cho một prompt."""
    if not memory_retrieval_enabled():
        return RetrievedMemory(text="", facts=[], episodes=[], enabled=False)

    store = store or default_memory_store()
    top_k = memory_top_k()
    fact_inventory = asks_for_fact_inventory(prompt)
    episode_inventory = asks_for_episode_inventory(prompt)
    # Câu hỏi "đang lưu fact nào" cần list inventory, không search theo chữ "fact".
    if fact_inventory and not fact_inventory_filter_words(prompt):
        facts = store.list_facts(top_k)
    else:
        facts = store.search_facts(prompt, top_k=top_k)
    if episode_inventory:
        episodes = store.recent_episodes(top_k)
    elif fact_inventory:
        episodes = []
    else:
        episodes = store.search_episodes(prompt, top_k=top_k)
    text = format_memory_context(facts, episodes, gateway_message=gateway_message)
    return RetrievedMemory(text=text, facts=facts, episodes=episodes, enabled=True)


def build_memory_context(prompt: str, gateway_message=None, store: MemoryStore | None = None) -> str:
    return retrieve_memory_context(prompt, gateway_message=gateway_message, store=store).text


def asks_for_fact_inventory(prompt: str) -> bool:
    normalized = normalize_text(prompt)
    return any(contains_keyword(normalized, keyword) for keyword in FACT_INVENTORY_KEYWORDS)


def asks_for_episode_inventory(prompt: str) -> bool:
    normalized = normalize_text(prompt)
    return any(contains_keyword(normalized, keyword) for keyword in EPISODE_INVENTORY_KEYWORDS)


def fact_inventory_filter_words(prompt: str) -> list[str]:
    normalized = normalize_text(prompt)
    words = re.findall(r"[\w]+", normalized, flags=re.UNICODE)
    return [word for word in words if len(word) >= 2 and word not in FACT_INVENTORY_FILTER_STOPWORDS]


def contains_keyword(normalized_text: str, keyword: str) -> bool:
    normalized_keyword = normalize_text(keyword)
    if not normalized_keyword:
        return False
    if len(normalized_keyword) <= 3 and normalized_keyword.isalnum():
        return normalized_keyword in normalized_text.split()
    return normalized_keyword in normalized_text


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    return normalized.replace("đ", "d")


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
    return memory_enabled() and os.getenv("NIKO_MEMORY_WRITE_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
