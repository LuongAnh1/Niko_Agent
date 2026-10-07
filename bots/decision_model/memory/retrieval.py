"""Decision task xác định Deep có cần đọc chat memory dài hạn không."""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any
import unicodedata

from bots.decision_model.client import ChoiceDecision, DecisionModelConfig, systemone_choice

from .common import normalize_optional_text, truncate_memory_gate_text
from .constants import (
    MAX_MEMORY_GATE_PROMPT_LENGTH,
    MEMORY_LIST_FACTS,
    MEMORY_RECENT_EPISODES,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_RETRIEVE,
    MEMORY_SKIP,
    QUESTION_NAME,
)
from .types import MemoryRetrievalDecision


def decide_memory_retrieval(
    prompt: str,
    gateway_message=None,
    config: DecisionModelConfig | None = None,
    choice_fn: Callable[..., ChoiceDecision] = systemone_choice,
) -> MemoryRetrievalDecision:
    """Hỏi Nimble xem turn Deep này có cần đọc chat memory dài hạn không."""
    decision = choice_fn(
        state=build_memory_retrieval_state(prompt, gateway_message),
        question_name=QUESTION_NAME,
        instructions=build_memory_retrieval_instructions(),
        criteria=build_memory_retrieval_criteria(),
        config=config,
    )
    normalized = apply_memory_retrieval_prompt_hint(prompt, normalize_memory_retrieval_choice(decision.choice))
    default_fact_mode, default_episode_mode = default_retrieval_modes(decision.choice, normalized)
    query = normalize_optional_text(decision.extra.get("query"))
    fact_mode = normalize_fact_retrieval_mode(decision.extra.get("fact_mode"), default=default_fact_mode)
    episode_mode = normalize_episode_retrieval_mode(decision.extra.get("episode_mode"), default=default_episode_mode)
    inventory_target = memory_inventory_prompt_target(prompt)
    if normalized == MEMORY_RETRIEVE and inventory_target == "facts":
        query = ""
        fact_mode = MEMORY_RETRIEVAL_LIST
        episode_mode = MEMORY_RETRIEVAL_NONE
    elif normalized == MEMORY_RETRIEVE and inventory_target == "episodes":
        query = ""
        fact_mode = MEMORY_RETRIEVAL_NONE
        episode_mode = MEMORY_RETRIEVAL_RECENT
    return MemoryRetrievalDecision(
        decision=normalized,
        query=query,
        reason=normalize_optional_text(decision.extra.get("reason")),
        fact_mode=fact_mode,
        episode_mode=episode_mode,
        confidence=decision.confidence,
        label=decision.choice,
        probabilities=decision.probabilities,
        model=decision.model,
        usage=decision.usage,
    )

def build_memory_retrieval_state(prompt: str, gateway_message=None) -> dict[str, Any]:
    """State tối thiểu: prompt hiện tại và metadata gateway không nhạy cảm."""
    state: dict[str, Any] = {"prompt": truncate_memory_gate_text(prompt)}
    if gateway_message is None:
        return state

    user = getattr(gateway_message, "user", None)
    state["gateway"] = {
        "platform": getattr(gateway_message, "platform", ""),
        "conversation_id": str(getattr(gateway_message, "chat_id", "") or getattr(user, "key", "")),
        "chat_type": getattr(gateway_message, "chat_type", ""),
        "user_key": getattr(user, "key", "") if user is not None else "",
        "user_label": getattr(user, "label", "") if user is not None else "",
    }
    return state

def build_memory_retrieval_instructions() -> str:
    """Luật retrieval gate: chỉ quyết định đọc memory hay bỏ qua."""
    return (
        "Choose whether Niko's Deep agent should retrieve long-term chat memory "
        "before answering this user message. Choose retrieve when the user asks "
        "about remembered facts, past conversations, preferences, people, projects, "
        "previous decisions, or anything that benefits from personal context. "
        "Choose skip for standalone questions, coding/debugging tasks with all "
        "needed context in the prompt, casual small talk, or generic knowledge. "
        "Choose list_facts for a general inventory question like 'what facts do "
        "you store about me?'. Choose recent_episodes for a general episodic "
        "inventory question like 'what recent events do you remember?'. "
        "If the API supports extra answer fields and you choose retrieve, include "
        "`query`, `fact_mode`, `episode_mode`, and `reason`. Use fact_mode=search "
        "for topic lookup, fact_mode=list for fact inventory, and fact_mode=none "
        "when facts are not needed. Use episode_mode=search for relevant past "
        "events, episode_mode=recent for episodic inventory, and episode_mode=none "
        "when episodes are not needed. Leave query empty for general inventory; "
        "otherwise use a concise semantic search query."
    )

def build_memory_retrieval_criteria() -> dict[str, str]:
    """Hai lựa chọn duy nhất mà memory pipeline hiểu ở v1."""
    return {
        MEMORY_SKIP: "Do not search long-term chat memory for this turn.",
        MEMORY_RETRIEVE: "Search long-term chat memory before building the Deep prompt.",
        MEMORY_LIST_FACTS: "List recent semantic facts for a general fact-inventory question.",
        MEMORY_RECENT_EPISODES: "List recent episodic events for a general episode-inventory question.",
    }

def normalize_memory_retrieval_choice(choice: str) -> str:
    """Chấp nhận alias nhẹ để model lệch chữ vẫn dùng được."""
    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        MEMORY_SKIP: MEMORY_SKIP,
        "no": MEMORY_SKIP,
        "none": MEMORY_SKIP,
        "no_memory": MEMORY_SKIP,
        "dont_retrieve": MEMORY_SKIP,
        "do_not_retrieve": MEMORY_SKIP,
        MEMORY_RETRIEVE: MEMORY_RETRIEVE,
        "search": MEMORY_RETRIEVE,
        "use_memory": MEMORY_RETRIEVE,
        "read_memory": MEMORY_RETRIEVE,
        "retrieve_memory": MEMORY_RETRIEVE,
        MEMORY_LIST_FACTS: MEMORY_RETRIEVE,
        "fact_inventory": MEMORY_RETRIEVE,
        "semantic_inventory": MEMORY_RETRIEVE,
        MEMORY_RECENT_EPISODES: MEMORY_RETRIEVE,
        "list_episodes": MEMORY_RETRIEVE,
        "episode_inventory": MEMORY_RETRIEVE,
        "episodic_inventory": MEMORY_RETRIEVE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory decision khong hop le: {choice or '(empty)'}") from exc


def apply_memory_retrieval_prompt_hint(prompt: str, decision: str) -> str:
    """Guardrail sau model: chỉ chỉnh mode inventory khi model đã mở retrieval."""
    if decision == MEMORY_RETRIEVE and memory_inventory_prompt_target(prompt):
        return MEMORY_RETRIEVE
    return decision


def memory_inventory_prompt_target(prompt: str) -> str:
    """Nhận diện prompt kiểm kê fact/episode để chọn đúng mode thực thi."""
    normalized = normalize_retrieval_prompt_text(prompt)
    if not normalized:
        return ""

    episode_inventory_markers = (
        "episode nao",
        "episodes nao",
        "episode gi",
        "episodes gi",
        "recent episode",
        "recent episodes",
        "episodic",
        "su kien nao",
        "su kien gi",
        "gan day em nho",
    )
    if any(marker in normalized for marker in episode_inventory_markers):
        return "episodes"

    fact_inventory_markers = (
        "fact nao",
        "facts nao",
        "fact gi",
        "facts gi",
        "nhung fact",
        "danh sach fact",
        "liet ke fact",
        "stored facts",
        "what facts",
        "which facts",
    )
    if any(marker in normalized for marker in fact_inventory_markers):
        return "facts"

    memory_refs = (
        "memory",
        "mem",
        "bo nho",
        "nho",
        "luu",
        "remember",
        "stored",
    )
    general_inventory_markers = (
        "biet gi ve",
        "nho gi ve",
        "luu gi ve",
        "dang luu",
        "da luu",
        "co luu",
        "co nhung",
        "danh sach",
        "liet ke",
        "list",
        "show",
        "xem",
        "inventory",
        "what do you know about",
        "remember about",
    )
    if any(ref in normalized for ref in memory_refs) and any(marker in normalized for marker in general_inventory_markers):
        return "facts"
    return ""


def normalize_retrieval_prompt_text(value: str) -> str:
    """Bỏ dấu/case và gom khoảng trắng cho guardrail tiếng Việt/Anh."""
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(char for char in decomposed if not unicodedata.combining(char))
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def default_retrieval_modes(choice: str, decision: str) -> tuple[str, str]:
    """Suy ra mode mặc định từ label choice mà Decision Model đã chọn."""
    if decision == MEMORY_SKIP:
        return MEMORY_RETRIEVAL_NONE, MEMORY_RETRIEVAL_NONE

    normalized = choice.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {MEMORY_LIST_FACTS, "fact_inventory", "semantic_inventory"}:
        return MEMORY_RETRIEVAL_LIST, MEMORY_RETRIEVAL_NONE
    if normalized in {MEMORY_RECENT_EPISODES, "list_episodes", "episode_inventory", "episodic_inventory"}:
        return MEMORY_RETRIEVAL_NONE, MEMORY_RETRIEVAL_RECENT
    return MEMORY_RETRIEVAL_SEARCH, MEMORY_RETRIEVAL_SEARCH


def normalize_fact_retrieval_mode(value: Any, *, default: str) -> str:
    """Chuẩn hóa mode lấy semantic facts do decision model trả về."""
    normalized = normalize_optional_text(value).lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return default
    aliases = {
        MEMORY_RETRIEVAL_SEARCH: MEMORY_RETRIEVAL_SEARCH,
        "topic_search": MEMORY_RETRIEVAL_SEARCH,
        "filter": MEMORY_RETRIEVAL_SEARCH,
        MEMORY_RETRIEVAL_LIST: MEMORY_RETRIEVAL_LIST,
        "all": MEMORY_RETRIEVAL_LIST,
        "inventory": MEMORY_RETRIEVAL_LIST,
        "list_facts": MEMORY_RETRIEVAL_LIST,
        MEMORY_RETRIEVAL_NONE: MEMORY_RETRIEVAL_NONE,
        "skip": MEMORY_RETRIEVAL_NONE,
        "no": MEMORY_RETRIEVAL_NONE,
        "no_facts": MEMORY_RETRIEVAL_NONE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory fact_mode khong hop le: {value}") from exc


def normalize_episode_retrieval_mode(value: Any, *, default: str) -> str:
    """Chuẩn hóa mode lấy episodic events do decision model trả về."""
    normalized = normalize_optional_text(value).lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return default
    aliases = {
        MEMORY_RETRIEVAL_SEARCH: MEMORY_RETRIEVAL_SEARCH,
        "topic_search": MEMORY_RETRIEVAL_SEARCH,
        "filter": MEMORY_RETRIEVAL_SEARCH,
        MEMORY_RETRIEVAL_RECENT: MEMORY_RETRIEVAL_RECENT,
        MEMORY_RETRIEVAL_LIST: MEMORY_RETRIEVAL_RECENT,
        "all": MEMORY_RETRIEVAL_RECENT,
        "inventory": MEMORY_RETRIEVAL_RECENT,
        "list_episodes": MEMORY_RETRIEVAL_RECENT,
        MEMORY_RETRIEVAL_NONE: MEMORY_RETRIEVAL_NONE,
        "skip": MEMORY_RETRIEVAL_NONE,
        "no": MEMORY_RETRIEVAL_NONE,
        "no_episodes": MEMORY_RETRIEVAL_NONE,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise RuntimeError(f"Ollama memory episode_mode khong hop le: {value}") from exc
