"""Contract public của Loop core V0.

Các dataclass trong file này là ranh giới chung giữa controller, runtime,
tool adapter, workflow domain và test. Chúng cố ý nhỏ, dễ serialize và không
nhúng khái niệm Telegram/Jira/memory cụ thể để Loop có thể dùng lại cho nhiều
lane nghiệp vụ.

`mutates_state` là tín hiệu quan sát/guardrail cho caller và dashboard, không
phải quyền tự động để tool được phép sửa dữ liệu. Workflow gọi tool vẫn phải tự
quyết định khi nào thao tác mutate là hợp lệ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol


@dataclass(frozen=True)
class ToolContext:
    """Metadata dùng chung trong một lượt loop, không phụ thuộc Telegram."""

    trace_id: str = ""
    conversation_id: str = ""
    user_key: str = ""
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Kết quả tool trả về cho loop quan sát ở iteration kế tiếp."""

    ok: bool
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""


ToolHandler = Callable[[dict[str, Any], ToolContext], ToolResult]


@dataclass(frozen=True)
class Tool:
    """Mô tả một tool và handler Python thật phía sau."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    mutates_state: bool = False

    def schema(self) -> dict[str, Any]:
        """Schema tối thiểu để controller/model biết cách gọi tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "mutates_state": self.mutates_state,
        }


@dataclass(frozen=True)
class LoopDecision:
    """Quyết định của controller: gọi tool hoặc trả final answer."""

    kind: Literal["tool", "final"]
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    reply: str = ""
    reason: str = ""

    @classmethod
    def tool(cls, tool_name: str, tool_args: dict[str, Any] | None = None, reason: str = "") -> "LoopDecision":
        return cls(kind="tool", tool_name=tool_name, tool_args=tool_args or {}, reason=reason)

    @classmethod
    def final(cls, reply: str, reason: str = "") -> "LoopDecision":
        return cls(kind="final", reply=reply, reason=reason)


@dataclass(frozen=True)
class LoopResult:
    """Kết quả cuối của một lượt loop."""

    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    limit_reached: bool = False
    error: str = ""


class LoopController(Protocol):
    """Protocol cho controller Python-controlled loop V0."""

    def __call__(
        self,
        prompt: str,
        context: ToolContext,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LoopDecision:
        ...
