"""Read-only Jira fixture tools cho Niko Loop.

Phase đầu chỉ đọc fixture local để chứng minh tool lane Jira tách khỏi chat
memory và lakehouse. Jira bot/gateway thật hoặc ingestion lakehouse là các lane
khác; tool ở đây chỉ fetch/normalize dữ liệu issue cho graph/Deep dùng.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from niko.loop import Tool, ToolContext, ToolResult


ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "demo_issues.json"
JIRA_FIXTURE_SOURCE = "jira_fixture:demo_issues.json"


JiraStoreProvider = Callable[[], "JiraFixtureStore"]


def extract_issue_keys(text: str) -> list[str]:
    """Trích issue key theo thứ tự xuất hiện, bỏ trùng, chuẩn hóa uppercase."""
    seen: set[str] = set()
    keys: list[str] = []
    for match in ISSUE_KEY_PATTERN.finditer(str(text or "").upper()):
        key = match.group(0)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def build_jira_issue_tools(store_provider: JiraStoreProvider | None = None) -> list[Tool]:
    """Tạo bộ Jira tools read-only cho LoopRegistry."""
    provider = store_provider or (lambda: JiraFixtureStore())
    adapter = JiraIssueTools(provider)
    return [
        Tool(
            name="parse_issue_key",
            description="Trích Jira issue key từ prompt.",
            input_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
            handler=adapter.parse_issue_key,
            mutates_state=False,
        ),
        Tool(
            name="fetch_jira_issue",
            description="Đọc thông tin chính của một Jira issue từ fixture.",
            input_schema={
                "type": "object",
                "properties": {"issue_key": {"type": "string"}},
                "required": ["issue_key"],
            },
            handler=adapter.fetch_issue,
            mutates_state=False,
        ),
        Tool(
            name="fetch_jira_comments",
            description="Đọc comments của một Jira issue từ fixture.",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["issue_key"],
            },
            handler=adapter.fetch_comments,
            mutates_state=False,
        ),
        Tool(
            name="fetch_jira_changelog",
            description="Đọc changelog/status history của một Jira issue từ fixture.",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["issue_key"],
            },
            handler=adapter.fetch_changelog,
            mutates_state=False,
        ),
    ]


class JiraFixtureStore:
    """Store read-only đọc issue từ fixture JSON local."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_FIXTURE_PATH
        self._issues: dict[str, dict[str, Any]] | None = None

    def get_issue(self, issue_key: str) -> dict[str, Any] | None:
        issue = self._load().get(_normalize_issue_key(issue_key))
        return deepcopy(issue) if issue is not None else None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._issues is not None:
            return self._issues
        with self.path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        issues = payload.get("issues", [])
        self._issues = {
            _normalize_issue_key(issue.get("key", "")): issue
            for issue in issues
            if isinstance(issue, dict) and _normalize_issue_key(issue.get("key", ""))
        }
        return self._issues


class JiraIssueTools:
    """Adapter mỏng từ Loop Tool contract sang Jira fixture store."""

    def __init__(self, store_provider: JiraStoreProvider) -> None:
        self._store_provider = store_provider

    @property
    def store(self) -> JiraFixtureStore:
        return self._store_provider()

    def parse_issue_key(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = _text_arg(args, "prompt")
        keys = extract_issue_keys(prompt)
        if not keys:
            return _error("issue_key_not_found", "Không tìm thấy Jira issue key trong prompt.", data={"issue_keys": []})
        return ToolResult(
            ok=True,
            text=f"Detected issue key: {keys[0]}",
            data={"issue_key": keys[0], "issue_keys": keys, "source": "prompt"},
        )

    def fetch_issue(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        issue_key = _normalize_issue_key(_text_arg(args, "issue_key"))
        issue = self.store.get_issue(issue_key)
        if issue is None:
            return _issue_not_found(issue_key)
        core = _issue_core(issue)
        return ToolResult(
            ok=True,
            text=_issue_text(core),
            data={"issue": core, "evidence": [_issue_evidence(core)], "source": JIRA_FIXTURE_SOURCE},
        )

    def fetch_comments(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        issue_key = _normalize_issue_key(_text_arg(args, "issue_key"))
        issue = self.store.get_issue(issue_key)
        if issue is None:
            return _issue_not_found(issue_key)
        limit = _bounded_int_arg(args, "limit", default=10, minimum=1, maximum=50)
        comments = list(issue.get("comments", []))[:limit]
        return ToolResult(
            ok=True,
            text=_comments_text(issue_key, comments),
            data={
                "issue_key": issue_key,
                "comments": comments,
                "limit": limit,
                "source": JIRA_FIXTURE_SOURCE,
            },
        )

    def fetch_changelog(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        issue_key = _normalize_issue_key(_text_arg(args, "issue_key"))
        issue = self.store.get_issue(issue_key)
        if issue is None:
            return _issue_not_found(issue_key)
        limit = _bounded_int_arg(args, "limit", default=20, minimum=1, maximum=50)
        changelog = list(issue.get("changelog", []))[:limit]
        return ToolResult(
            ok=True,
            text=_changelog_text(issue_key, changelog),
            data={
                "issue_key": issue_key,
                "changelog": changelog,
                "limit": limit,
                "source": JIRA_FIXTURE_SOURCE,
            },
        )


def _issue_core(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(issue.get(key))
        for key in (
            "key",
            "project",
            "summary",
            "description",
            "type",
            "status",
            "priority",
            "assignee",
            "reporter",
            "components",
            "labels",
            "links",
        )
    }


def _issue_evidence(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": JIRA_FIXTURE_SOURCE,
        "issue_key": issue.get("key", ""),
        "fields": ["summary", "description", "status", "priority", "components", "links"],
    }


def _issue_text(issue: dict[str, Any]) -> str:
    components = ", ".join(issue.get("components") or []) or "-"
    return (
        f"Issue {issue.get('key')}: {issue.get('summary')}\n"
        f"Status: {issue.get('status')} | Priority: {issue.get('priority')} | Components: {components}"
    )


def _comments_text(issue_key: str, comments: list[dict[str, Any]]) -> str:
    if not comments:
        return f"Issue {issue_key} chưa có comment trong fixture."
    lines = [f"Comments for {issue_key}:"]
    lines.extend(f"- {item.get('id')}: {item.get('author')} - {item.get('body')}" for item in comments)
    return "\n".join(lines)


def _changelog_text(issue_key: str, changelog: list[dict[str, Any]]) -> str:
    if not changelog:
        return f"Issue {issue_key} chưa có changelog trong fixture."
    lines = [f"Changelog for {issue_key}:"]
    lines.extend(
        f"- {item.get('timestamp')}: {item.get('field')} {item.get('from')} -> {item.get('to')}"
        for item in changelog
    )
    return "\n".join(lines)


def _normalize_issue_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _text_arg(args: dict[str, Any], key: str) -> str:
    return str(args.get(key, "") or "").strip()


def _bounded_int_arg(args: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _issue_not_found(issue_key: str) -> ToolResult:
    return _error(
        "issue_not_found",
        f"Không có dữ liệu Jira fixture cho issue {issue_key}.",
        data={"issue_key": issue_key, "source": JIRA_FIXTURE_SOURCE},
    )


def _error(error: str, text: str, data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(ok=False, text=text, data=data or {}, error=error)


__all__ = ["JiraFixtureStore", "JiraIssueTools", "build_jira_issue_tools", "extract_issue_keys"]
