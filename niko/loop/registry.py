"""Registry thực thi tool cho Loop core V0.

Registry chỉ giữ danh sách tool đã chuẩn hóa tên, expose schema cho controller
và bọc lời gọi handler thành `ToolResult`. Nó không quyết định prompt nào được
dùng tool, tool nào đủ an toàn, hay reply cuối cùng nên nói gì; các policy đó
thuộc workflow domain như memory correction hoặc Jira issue analysis.
"""

from __future__ import annotations

from typing import Any

from niko.loop.types import Tool, ToolContext, ToolResult


class ToolRegistry:
    """Kho tool theo tên, có validate nhẹ và fail-safe khi handler lỗi."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        name = tool.name.strip()
        if not name:
            raise ValueError("Tool name must not be empty.")
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        normalized = Tool(
            name=name,
            description=tool.description,
            input_schema=tool.input_schema,
            handler=tool.handler,
            mutates_state=tool.mutates_state,
        )
        self._tools[name] = normalized
        return normalized

    def get(self, name: str) -> Tool | None:
        return self._tools.get(str(name or "").strip())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any] | None, context: ToolContext) -> ToolResult:
        tool_name = str(name or "").strip()
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(ok=False, text=f"Unknown tool: {tool_name}", error="unknown_tool")

        if args is not None and not isinstance(args, dict):
            return ToolResult(ok=False, text="Tool args must be an object.", error="invalid_tool_args")

        try:
            result = tool.handler(dict(args or {}), context)
        except Exception as exc:
            return ToolResult(ok=False, text=f"Tool failed: {exc}", error=str(exc))

        if not isinstance(result, ToolResult):
            return ToolResult(
                ok=False,
                text="Tool returned an invalid result.",
                data={"return_type": type(result).__name__},
                error="invalid_tool_result",
            )
        return result

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None
