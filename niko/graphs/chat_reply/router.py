from __future__ import annotations

from dataclasses import dataclass
import unicodedata


ROUTE_LOCAL_REPLY = "local_reply"
ROUTE_FAST_AGENT = "fast_agent"
ROUTE_DEEP_AGENT = "deep_agent"
ROUTE_BUSY_REPLY = "busy_reply"
ROUTE_DELAYED_DEEP_AGENT = "delayed_deep_agent"


@dataclass(frozen=True)
class AgentRoute:
    kind: str
    reply: str = ""
    reason: str = ""


ACK_VALUES = {"ok", "oke", "okay", "umk", "uk", "uh", "uhm", "vang", "da"}
GREETING_KEYWORDS = ("chao", "hello", "hi", "alo", "em oi", "niko oi")
THANKS_KEYWORDS = ("cam on", "thanks", "thank you", "thank")
PRAISE_KEYWORDS = (
    "tot lam",
    "gioi",
    "hay qua",
    "ngon",
    "de thuong",
    "good job",
    "well done",
)
DEEP_KEYWORDS = (
    "phan tich",
    "thiet ke",
    "debug",
    "sua loi",
    "sua code",
    "viet code",
    "refactor",
    "review",
    "kiem tra",
    "so sanh",
    "tim hieu",
    "trien khai",
    "cau hinh",
    "memory",
    "tool",
    "mcp",
    "api",
    "context",
    "telegram",
    "zalo",
    "github",
    "server",
    "deploy",
    "render",
    "pull request",
    "pr",
    "repo",
    "hook",
)


def decide_agent_route(
    prompt: str,
    deep_job_active: bool = False,
    fast_agent_available: bool = False,
) -> AgentRoute:
    normalized = normalize_text(prompt)
    if deep_job_active:
        return AgentRoute(ROUTE_BUSY_REPLY, reason="deep_job_active")

    local_reply = build_local_reply(normalized)
    if local_reply:
        return AgentRoute(ROUTE_LOCAL_REPLY, local_reply, "local_rule")

    if should_use_deep_agent(prompt):
        return AgentRoute(ROUTE_DEEP_AGENT, reason="needs_deep_analysis")

    if fast_agent_available:
        return AgentRoute(ROUTE_FAST_AGENT, reason="fast_agent_available")

    return AgentRoute(ROUTE_DELAYED_DEEP_AGENT, reason="uncertain_no_fast_agent")


def build_local_reply(normalized_prompt: str) -> str:
    compact = normalized_prompt.strip()
    if not compact:
        return ""

    if compact in ACK_VALUES:
        return "D\u1ea1 v\u00e2ng anh."

    if compact == "ping" or compact.startswith("ping "):
        return "D\u1ea1 em c\u00f2n \u0111\u00e2y anh."

    if len(compact) <= 60 and any(contains_keyword(compact, keyword) for keyword in GREETING_KEYWORDS):
        return "D\u1ea1 em \u0111\u00e2y anh."

    if len(compact) <= 90 and any(contains_keyword(compact, keyword) for keyword in THANKS_KEYWORDS):
        return "D\u1ea1 kh\u00f4ng c\u00f3 g\u00ec anh."

    if len(compact) <= 90 and any(contains_keyword(compact, keyword) for keyword in PRAISE_KEYWORDS):
        return "D\u1ea1 em c\u1ea3m \u01a1n anh, em vui l\u1eafm."

    return ""


def should_use_deep_agent(prompt: str) -> bool:
    normalized = normalize_text(prompt)
    if len(normalized) >= 220:
        return True
    if "\n" in prompt or "`" in prompt:
        return True
    if any(contains_keyword(normalized, keyword) for keyword in DEEP_KEYWORDS):
        return True
    return False


def contains_keyword(normalized_text: str, keyword: str) -> bool:
    normalized_keyword = normalize_text(keyword)
    if not normalized_keyword:
        return False
    if len(normalized_keyword) <= 3 and normalized_keyword.isalnum():
        return normalized_keyword in normalized_text.split()
    return normalized_keyword in normalized_text


def normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


__all__ = [
    "ACK_VALUES",
    "DEEP_KEYWORDS",
    "GREETING_KEYWORDS",
    "PRAISE_KEYWORDS",
    "ROUTE_BUSY_REPLY",
    "ROUTE_DEEP_AGENT",
    "ROUTE_DELAYED_DEEP_AGENT",
    "ROUTE_FAST_AGENT",
    "ROUTE_LOCAL_REPLY",
    "THANKS_KEYWORDS",
    "AgentRoute",
    "build_local_reply",
    "contains_keyword",
    "decide_agent_route",
    "normalize_text",
    "should_use_deep_agent",
]
