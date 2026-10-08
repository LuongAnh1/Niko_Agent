"""Workflow phân tích Jira issue bằng Loop tools read-only.

File này là workflow nghiệp vụ được `NikoApp` chọn trước normal chat:
LoopRuntime gọi tool theo từng bước, rồi workflow chuẩn hóa evidence thành
context cho Deep agent. Tool chỉ đọc dữ liệu fixture/API; phần trả lời cuối cùng
vẫn thuộc Deep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bots.decision_model.jira import (
    JIRA_ASK_FOR_ISSUE_KEY,
    JIRA_SKIP,
    JIRA_USE_TOOL,
    JiraGateDecision,
    decide_jira_gate,
)
from niko.config import env_flag, env_value, resolve_project_path
from niko.harness.runtime_log import default_runtime_logger
from niko.loop import LoopDecision, LoopResult, LoopRuntime, ToolContext, ToolRegistry, TraceLoopObserver
from niko.tools.jira import JiraFixtureStore, build_jira_issue_tools, extract_issue_keys


ROUTE_JIRA_ISSUE_DEEP_AGENT = "jira_issue_deep_agent"
ROUTE_JIRA_ISSUE_NOT_FOUND = "jira_issue_not_found"
ROUTE_JIRA_ISSUE_KEY_REQUIRED = "jira_issue_key_required"
DEFAULT_JIRA_LOOP_MAX_ITERATIONS = 5
DEFAULT_JIRA_DECISION_CONFIDENCE_THRESHOLD = 0.75
DEFAULT_JIRA_DECISION_RECENT_TURNS = 4


@dataclass(frozen=True)
class JiraIssueAnalysisResult:
    """Kết quả chuẩn hóa để NikoApp biết nên gọi Deep hay trả lỗi ngay."""

    handled: bool
    route: str = ""
    issue_key: str = ""
    issue_keys: list[str] = field(default_factory=list)
    deep_context: str = ""
    wait_reply: str = ""
    reply: str = ""
    error: str = ""
    gate: dict[str, Any] = field(default_factory=dict)
    loop_result: LoopResult | None = None


class JiraIssueAnalysisWorkflow:
    """Chạy parse/fetch Jira fixture qua Loop rồi build context có evidence."""

    def __init__(
        self,
        *,
        fixture_path: str | Path | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.fixture_path = Path(fixture_path) if fixture_path is not None else _configured_fixture_path()
        self.max_iterations = max(DEFAULT_JIRA_LOOP_MAX_ITERATIONS, max_iterations or _configured_max_iterations())

    def handle(
        self,
        *,
        prompt: str,
        conversation_id: str,
        user_key: str,
        trace_id: str,
        trace_logger=None,
        gateway_message=None,
        recent_turns: list[dict[str, Any]] | None = None,
    ) -> JiraIssueAnalysisResult:
        """Trả `handled=False` nếu prompt không thuộc Jira flow để graph đi route cũ."""
        issue_keys = extract_issue_keys(prompt)
        recent_limit = jira_decision_recent_turns()
        recent_turns = list(recent_turns or [])[-recent_limit:] if recent_limit else []
        active_gate: dict[str, Any] = {}
        if not issue_keys:
            gate_result = self._evaluate_decision_gate(
                prompt=prompt,
                recent_turns=recent_turns,
                gateway_message=gateway_message,
                trace_id=trace_id,
                trace_logger=trace_logger,
            )
            if not gate_result.handled:
                return gate_result
            issue_keys = gate_result.issue_keys
            if not issue_keys:
                return gate_result
            active_gate = gate_result.gate
        else:
            active_gate = _rule_gate_state(issue_keys[0], issue_keys)
            _trace_gate(trace_logger, trace_id, active_gate)
            _log_jira_gate(active_gate)

        effective_prompt = prompt
        if issue_keys and not extract_issue_keys(prompt):
            effective_prompt = f"{prompt}\n\nJira issue key from recent context: {issue_keys[0]}"
        registry = ToolRegistry(build_jira_issue_tools(store_provider=lambda: self._store()))
        observer = self._observer(trace_logger, trace_id)
        runtime = LoopRuntime(
            JiraIssueLoopController(effective_prompt),
            registry,
            max_iterations=self.max_iterations,
            observer=observer,
        )
        loop_result = runtime.run(
            effective_prompt,
            ToolContext(
                trace_id=trace_id,
                conversation_id=conversation_id,
                user_key=user_key,
                state={"workflow": "jira_issue_analysis"},
            ),
        )

        issue_key = _first_issue_key(loop_result) or issue_keys[0]
        if loop_result.error or loop_result.limit_reached:
            reply = "Dạ workflow Jira chưa lấy đủ dữ liệu issue, nên em chưa phân tích được anh."
            return JiraIssueAnalysisResult(
                True,
                route=ROUTE_JIRA_ISSUE_NOT_FOUND,
                issue_key=issue_key,
                issue_keys=issue_keys,
                reply=reply,
                error=loop_result.error or "loop_limit_reached",
                gate=active_gate,
                loop_result=loop_result,
            )

        issue_data = _tool_data(loop_result, "fetch_jira_issue")
        if not issue_data:
            error = _tool_error(loop_result, "fetch_jira_issue") or "issue_not_found"
            reply = f"Dạ em chưa có dữ liệu Jira fixture cho issue {issue_key}, nên chưa phân tích được anh."
            return JiraIssueAnalysisResult(
                True,
                route=ROUTE_JIRA_ISSUE_NOT_FOUND,
                issue_key=issue_key,
                issue_keys=issue_keys,
                reply=reply,
                error=error,
                gate=active_gate,
                loop_result=loop_result,
            )

        context = build_jira_issue_context(
            issue=issue_data.get("issue") or {},
            comments=(_tool_data(loop_result, "fetch_jira_comments") or {}).get("comments") or [],
            changelog=(_tool_data(loop_result, "fetch_jira_changelog") or {}).get("changelog") or [],
            evidence=issue_data.get("evidence") or [],
            source=str(issue_data.get("source") or ""),
        )
        wait_reply = f"Dạ anh đợi em chút, em đã lấy context Jira của {issue_key} rồi, giờ em phân tích bằng Deep."
        return JiraIssueAnalysisResult(
            True,
            route=ROUTE_JIRA_ISSUE_DEEP_AGENT,
            issue_key=issue_key,
            issue_keys=issue_keys,
            deep_context=context,
            wait_reply=wait_reply,
            gate=active_gate,
            loop_result=loop_result,
        )

    def _evaluate_decision_gate(
        self,
        *,
        prompt: str,
        recent_turns: list[dict[str, Any]],
        gateway_message,
        trace_id: str,
        trace_logger,
    ) -> JiraIssueAnalysisResult:
        """Chỉ hỏi Nimble ở vùng mơ hồ; lỗi gate fallback về route cũ."""
        recent_issue_keys = _issue_keys_from_recent(recent_turns)
        if not jira_decision_gate_enabled():
            return JiraIssueAnalysisResult(False)
        if not _looks_like_jira_prompt(prompt, recent_issue_keys):
            return JiraIssueAnalysisResult(False)
        if recent_issue_keys and _references_recent_jira_issue(prompt):
            issue_key = recent_issue_keys[0]
            gate = _rule_gate_state(issue_key, recent_issue_keys, reason="recent_issue_reference")
            _trace_gate(trace_logger, trace_id, gate)
            _log_jira_gate(gate)
            return JiraIssueAnalysisResult(True, issue_key=issue_key, issue_keys=[issue_key], gate=gate)

        gate: dict[str, Any] = {
            "enabled": True,
            "provider": "ollama_nimble",
            "decision": JIRA_SKIP,
            "issue_key": "",
            "issue_keys": recent_issue_keys,
            "query": "",
            "reason": "",
            "confidence": None,
            "label": "",
            "probabilities": {},
            "model": "",
            "error": "",
            "source": "decision_model",
        }
        try:
            decision = decide_jira_gate(
                prompt,
                issue_keys=[],
                recent_turns=recent_turns,
                gateway_message=gateway_message,
            )
        except Exception as exc:
            gate.update({"error": str(exc), "reason": "jira_gate_error_fallback"})
            _trace_gate_error(trace_logger, trace_id, gate)
            _log_jira_gate(gate, level="warning")
            return JiraIssueAnalysisResult(False, gate=gate)

        gate.update(_gate_meta(decision))
        if _below_confidence_threshold(decision):
            gate["reason"] = gate["reason"] or "confidence_below_threshold"
            _trace_gate(trace_logger, trace_id, gate)
            _log_jira_gate(gate)
            return JiraIssueAnalysisResult(False, gate=gate)

        if decision.decision == JIRA_SKIP:
            _trace_gate(trace_logger, trace_id, gate)
            _log_jira_gate(gate)
            return JiraIssueAnalysisResult(False, gate=gate)

        issue_key = _valid_issue_key(decision.issue_key) or (recent_issue_keys[0] if recent_issue_keys else "")
        if decision.decision == JIRA_USE_TOOL and issue_key:
            gate["issue_key"] = issue_key
            gate["issue_keys"] = [issue_key, *[key for key in recent_issue_keys if key != issue_key]]
            _trace_gate(trace_logger, trace_id, gate)
            _log_jira_gate(gate)
            return JiraIssueAnalysisResult(True, issue_key=issue_key, issue_keys=[issue_key], gate=gate)

        gate["decision"] = JIRA_ASK_FOR_ISSUE_KEY
        gate["reason"] = gate["reason"] or "issue_key_required"
        _trace_gate(trace_logger, trace_id, gate)
        _log_jira_gate(gate)
        return JiraIssueAnalysisResult(
            True,
            route=ROUTE_JIRA_ISSUE_KEY_REQUIRED,
            reply="Dạ anh gửi giúp em mã issue Jira cụ thể, ví dụ `NIKO-101`, rồi em sẽ lấy context để phân tích tiếp nhé.",
            gate=gate,
        )

    def _store(self) -> JiraFixtureStore:
        return JiraFixtureStore(self.fixture_path) if self.fixture_path is not None else JiraFixtureStore()

    @staticmethod
    def _observer(trace_logger, trace_id: str):
        if trace_logger is None:
            return None
        return TraceLoopObserver(
            trace_logger,
            trace_id=trace_id,
            runtime_logger=default_runtime_logger(),
            runtime_source="loop",
        )


class JiraIssueLoopController:
    """Controller deterministic V0: parse key -> fetch issue -> comments -> changelog."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.issue_key = ""

    def __call__(
        self,
        prompt: str,
        context: ToolContext,
        history: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LoopDecision:
        if not history:
            return LoopDecision.tool("parse_issue_key", {"prompt": prompt}, reason="find_issue_key")

        last_tool = _last_tool_result(history)
        if last_tool is None:
            return LoopDecision.final("jira_context_unavailable", reason="invalid_loop_history")

        tool_name = str(last_tool.get("tool_name") or "")
        data = last_tool.get("data") if isinstance(last_tool.get("data"), dict) else {}
        if tool_name == "parse_issue_key":
            if not last_tool.get("ok"):
                return LoopDecision.final("jira_issue_key_not_found", reason="issue_key_not_found")
            self.issue_key = str(data.get("issue_key") or "").strip()
            return LoopDecision.tool("fetch_jira_issue", {"issue_key": self.issue_key}, reason="fetch_issue")

        if tool_name == "fetch_jira_issue":
            if not last_tool.get("ok"):
                return LoopDecision.final("jira_issue_not_found", reason=str(last_tool.get("error") or "issue_not_found"))
            return LoopDecision.tool("fetch_jira_comments", {"issue_key": self.issue_key}, reason="fetch_comments")

        if tool_name == "fetch_jira_comments":
            return LoopDecision.tool("fetch_jira_changelog", {"issue_key": self.issue_key}, reason="fetch_changelog")

        if tool_name == "fetch_jira_changelog":
            return LoopDecision.final("jira_context_ready", reason="context_ready")

        return LoopDecision.final("jira_context_unavailable", reason="unknown_tool_result")


def jira_tools_enabled() -> bool:
    """Cờ dashboard cho phép route prompt có issue key sang Jira workflow V0."""
    return env_flag("NIKO_JIRA_TOOLS_ENABLED", "0")


def jira_decision_gate_enabled() -> bool:
    """Cờ riêng cho Decision Model ở vùng prompt Jira mơ hồ."""
    return env_flag("NIKO_JIRA_DECISION_GATE_ENABLED", "0")


def jira_decision_confidence_threshold() -> float:
    raw_value = env_value(
        "NIKO_JIRA_DECISION_CONFIDENCE_THRESHOLD",
        str(DEFAULT_JIRA_DECISION_CONFIDENCE_THRESHOLD),
    ).strip()
    try:
        return max(0.0, min(1.0, float(raw_value)))
    except ValueError:
        return DEFAULT_JIRA_DECISION_CONFIDENCE_THRESHOLD


def jira_decision_recent_turns() -> int:
    raw_value = env_value("NIKO_JIRA_DECISION_RECENT_TURNS", str(DEFAULT_JIRA_DECISION_RECENT_TURNS)).strip()
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_JIRA_DECISION_RECENT_TURNS


def build_jira_issue_context(
    *,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    changelog: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    source: str,
) -> str:
    """Format context gọn, có source/evidence để Deep không phải đoán."""
    labels = ", ".join(issue.get("labels") or []) or "-"
    components = ", ".join(issue.get("components") or []) or "-"
    project = issue.get("project") if isinstance(issue.get("project"), dict) else {}
    lines = [
        "Jira issue context (read-only fixture):",
        f"- source: {source or '-'}",
        f"- issue_key: {issue.get('key') or '-'}",
        f"- project: {project.get('key') or '-'} - {project.get('name') or '-'}",
        f"- type/status/priority: {issue.get('type') or '-'} / {issue.get('status') or '-'} / {issue.get('priority') or '-'}",
        f"- assignee: {issue.get('assignee') or '-'}",
        f"- reporter: {issue.get('reporter') or '-'}",
        f"- components: {components}",
        f"- labels: {labels}",
        f"- summary: {issue.get('summary') or '-'}",
        f"- description: {issue.get('description') or '-'}",
        "",
        "Links:",
        *_format_links(issue.get("links") or []),
        "",
        "Comments:",
        *_format_comments(comments),
        "",
        "Changelog:",
        *_format_changelog(changelog),
        "",
        "Evidence:",
        *_format_evidence(evidence),
        "",
        "Instruction for Deep:",
        "- Analyze only from this Jira context plus the current user message.",
        "- If the fixture is missing a field, say that the available evidence is incomplete.",
    ]
    return "\n".join(lines).strip()


def _configured_fixture_path() -> Path | None:
    raw_path = env_value("NIKO_JIRA_FIXTURE_PATH", "").strip()
    return resolve_project_path(raw_path) if raw_path else None


def _configured_max_iterations() -> int:
    raw_value = env_value("NIKO_JIRA_LOOP_MAX_ITERATIONS", str(DEFAULT_JIRA_LOOP_MAX_ITERATIONS)).strip()
    try:
        return int(raw_value)
    except ValueError:
        return DEFAULT_JIRA_LOOP_MAX_ITERATIONS


def _rule_gate_state(
    issue_key: str,
    issue_keys: list[str],
    *,
    reason: str = "issue_key_in_prompt",
) -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "python_rule",
        "decision": JIRA_USE_TOOL,
        "issue_key": issue_key,
        "issue_keys": issue_keys,
        "query": "",
        "reason": reason,
        "confidence": None,
        "label": reason,
        "probabilities": {},
        "model": "",
        "error": "",
        "source": "rule",
    }


def _gate_meta(decision: JiraGateDecision) -> dict[str, Any]:
    return {
        "decision": decision.decision,
        "issue_key": decision.issue_key,
        "query": decision.query,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "label": decision.label,
        "probabilities": decision.probabilities,
        "model": decision.model,
        "usage": decision.usage,
    }


def _below_confidence_threshold(decision: JiraGateDecision) -> bool:
    return decision.confidence is not None and decision.confidence < jira_decision_confidence_threshold()


def _valid_issue_key(value: str) -> str:
    keys = extract_issue_keys(value)
    return keys[0] if keys else ""


def _issue_keys_from_recent(recent_turns: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for turn in reversed(recent_turns):
        for key in extract_issue_keys(str(turn.get("content") or "")):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


JIRA_SIGNAL_WORDS = {
    "jira",
    "issue",
    "ticket",
    "task",
    "bug",
    "sprint",
    "comment",
    "changelog",
    "status",
    "assignee",
    "reporter",
    "workflow",
    "reopen",
    "block",
    "blocked",
}

JIRA_SIGNAL_PHRASES = (
    "mã issue",
    "ma issue",
    "issue key",
    "jira issue",
    "vé này",
    "ticket này",
    "task này",
    "lỗi này",
    "bug này",
    "comment nào",
    "trạng thái",
    "vừa nãy",
    "vừa rồi",
)


JIRA_RECENT_REFERENCE_WORDS = {"jira", "issue", "ticket", "task", "bug", "vé", "lỗi"}
JIRA_RECENT_REFERENCE_PHRASES = ("vừa nãy", "vừa rồi", "lúc nãy", "ban nãy", "trước đó", "đó", "nay", "này")


def _looks_like_jira_prompt(prompt: str, recent_issue_keys: list[str]) -> bool:
    if recent_issue_keys and any(phrase in prompt.lower() for phrase in ("vừa nãy", "vừa rồi", "đó", "nay", "này")):
        return True
    lowered = prompt.lower()
    words = {word.strip(".,:;!?`\"'()[]{}") for word in lowered.split()}
    return bool(words & JIRA_SIGNAL_WORDS) or any(phrase in lowered for phrase in JIRA_SIGNAL_PHRASES)


def _references_recent_jira_issue(prompt: str) -> bool:
    lowered = prompt.lower()
    if not any(phrase in lowered for phrase in JIRA_RECENT_REFERENCE_PHRASES):
        return False
    words = {word.strip(".,:;!?`\"'()[]{}") for word in lowered.split()}
    return bool(words & JIRA_RECENT_REFERENCE_WORDS)


def _trace_gate(trace_logger, trace_id: str, gate: dict[str, Any]) -> None:
    if trace_logger is None:
        return
    try:
        trace_logger.event(trace_id, "jira_gate_decision", gate)
    except Exception:
        pass


def _trace_gate_error(trace_logger, trace_id: str, gate: dict[str, Any]) -> None:
    if trace_logger is None:
        return
    try:
        trace_logger.event(trace_id, "jira_gate_error", gate)
    except Exception:
        pass


def _log_jira_gate(gate: dict[str, Any], *, level: str = "info") -> None:
    try:
        default_runtime_logger().event(
            "jira",
            "jira_gate_decision" if not gate.get("error") else "jira_gate_error",
            (
                "Jira gate: "
                f"provider={gate.get('provider') or '-'} "
                f"decision={gate.get('decision') or '-'} "
                f"issue_key={gate.get('issue_key') or '-'} "
                f"confidence={_format_confidence(gate.get('confidence'))} "
                f"reason={gate.get('reason') or '-'}"
            ),
            level=level,
            data={key: value for key, value in gate.items() if value not in ("", None, {})},
        )
    except Exception:
        pass


def _format_confidence(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "-"


def _last_tool_result(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in reversed(history):
        if item.get("type") == "tool_result":
            return item
    return None


def _tool_data(loop_result: LoopResult, tool_name: str) -> dict[str, Any] | None:
    for call in loop_result.tool_calls:
        if call.get("tool_name") == tool_name and call.get("ok") and isinstance(call.get("data"), dict):
            return call["data"]
    return None


def _tool_error(loop_result: LoopResult, tool_name: str) -> str:
    for call in loop_result.tool_calls:
        if call.get("tool_name") == tool_name and not call.get("ok"):
            return str(call.get("error") or "")
    return ""


def _first_issue_key(loop_result: LoopResult) -> str:
    data = _tool_data(loop_result, "parse_issue_key") or {}
    return str(data.get("issue_key") or "").strip()


def _format_links(links: list[dict[str, Any]]) -> list[str]:
    if not links:
        return ["- none"]
    return [
        f"- {item.get('type') or '-'} {item.get('key') or '-'}: {item.get('summary') or '-'}"
        for item in links
        if isinstance(item, dict)
    ] or ["- none"]


def _format_comments(comments: list[dict[str, Any]]) -> list[str]:
    if not comments:
        return ["- none"]
    return [
        f"- {item.get('id') or '-'} | {item.get('author') or '-'} | {item.get('created_at') or '-'}: {item.get('body') or '-'}"
        for item in comments
        if isinstance(item, dict)
    ] or ["- none"]


def _format_changelog(changelog: list[dict[str, Any]]) -> list[str]:
    if not changelog:
        return ["- none"]
    return [
        (
            f"- {item.get('timestamp') or '-'} | {item.get('author') or '-'} | "
            f"{item.get('field') or '-'}: {item.get('from') or '-'} -> {item.get('to') or '-'}"
        )
        for item in changelog
        if isinstance(item, dict)
    ] or ["- none"]


def _format_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return ["- source fields are unavailable"]
    lines: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        fields = ", ".join(item.get("fields") or []) or "-"
        lines.append(f"- {item.get('source') or '-'} | issue={item.get('issue_key') or '-'} | fields={fields}")
    return lines or ["- source fields are unavailable"]
