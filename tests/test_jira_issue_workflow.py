import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.decision_model.jira import JIRA_ASK_FOR_ISSUE_KEY, JIRA_SKIP, JIRA_USE_TOOL, JiraGateDecision
from niko.graphs.jira_issue import (
    ROUTE_JIRA_ISSUE_DEEP_AGENT,
    ROUTE_JIRA_ISSUE_KEY_REQUIRED,
    ROUTE_JIRA_ISSUE_NOT_FOUND,
    JiraIssueAnalysisWorkflow,
)
from niko.harness.trace import TraceLogger


class JiraIssueAnalysisWorkflowTests(unittest.TestCase):
    def test_valid_issue_key_builds_deep_context_with_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            workflow = JiraIssueAnalysisWorkflow()

            result = workflow.handle(
                prompt="phân tích NIKO-101 giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
                trace_logger=trace_logger,
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertEqual(result.issue_key, "NIKO-101")
        self.assertIn("Jira issue context", result.deep_context)
        self.assertIn("Memory correction loop asks", result.deep_context)
        self.assertIn("Evidence:", result.deep_context)
        self.assertEqual(
            [call["tool_name"] for call in result.loop_result.tool_calls],
            ["parse_issue_key", "fetch_jira_issue", "fetch_jira_comments", "fetch_jira_changelog"],
        )
        self.assertFalse(any(call["mutates_state"] for call in result.loop_result.tool_calls))

    def test_prompt_without_issue_key_is_not_handled(self):
        result = JiraIssueAnalysisWorkflow().handle(
            prompt="phân tích task này giúp anh",
            conversation_id="chat-1",
            user_key="telegram:1",
            trace_id="trace-1",
        )

        self.assertFalse(result.handled)

    def test_clear_issue_key_does_not_call_decision_gate(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate"
        ) as gate:
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="phân tích NIKO-101 giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        gate.assert_not_called()

    def test_ambiguous_prompt_with_skip_gate_is_not_handled(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_SKIP, confidence=0.9),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket này giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertFalse(result.handled)
        self.assertEqual(result.gate["decision"], JIRA_SKIP)

    def test_ambiguous_prompt_asks_for_issue_key_when_gate_requests_it(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_ASK_FOR_ISSUE_KEY, confidence=0.92),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket này giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_KEY_REQUIRED)
        self.assertIn("mã issue Jira", result.reply)
        self.assertIsNone(result.loop_result)

    def test_use_jira_tool_without_valid_key_asks_for_issue_key(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_USE_TOOL, confidence=0.92),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket này giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_KEY_REQUIRED)
        self.assertEqual(result.deep_context, "")

    def test_use_jira_tool_can_take_issue_key_from_recent_turns(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_USE_TOOL, confidence=0.92),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket vừa nãy giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
                recent_turns=[{"role": "user", "content": "phân tích NIKO-101"}],
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertEqual(result.issue_key, "NIKO-101")
        self.assertIn("Jira issue context", result.deep_context)

    def test_recent_issue_reference_uses_python_rule_before_model(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate"
        ) as gate:
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket vừa nãy giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
                recent_turns=[{"role": "user", "content": "phân tích NIKO-101"}],
            )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_DEEP_AGENT)
        self.assertEqual(result.issue_key, "NIKO-101")
        self.assertEqual(result.gate["provider"], "python_rule")
        self.assertEqual(result.gate["reason"], "recent_issue_reference")
        gate.assert_not_called()

    def test_low_confidence_gate_falls_back_to_old_route(self):
        with patch.dict(
            "os.environ",
            {"NIKO_JIRA_DECISION_GATE_ENABLED": "1", "NIKO_JIRA_DECISION_CONFIDENCE_THRESHOLD": "0.75"},
            clear=False,
        ), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            return_value=JiraGateDecision(decision=JIRA_USE_TOOL, confidence=0.4, issue_key="NIKO-101"),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket này giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertFalse(result.handled)
        self.assertEqual(result.gate["reason"], "confidence_below_threshold")

    def test_gate_error_falls_back_to_old_route(self):
        with patch.dict("os.environ", {"NIKO_JIRA_DECISION_GATE_ENABLED": "1"}, clear=False), patch(
            "niko.graphs.jira_issue.workflow.decide_jira_gate",
            side_effect=RuntimeError("ollama down"),
        ):
            result = JiraIssueAnalysisWorkflow().handle(
                prompt="xem ticket này giúp anh",
                conversation_id="chat-1",
                user_key="telegram:1",
                trace_id="trace-1",
            )

        self.assertFalse(result.handled)
        self.assertEqual(result.gate["reason"], "jira_gate_error_fallback")

    def test_missing_issue_key_returns_safe_reply_without_deep_context(self):
        result = JiraIssueAnalysisWorkflow().handle(
            prompt="phân tích NIKO-404 giúp anh",
            conversation_id="chat-1",
            user_key="telegram:1",
            trace_id="trace-1",
        )

        self.assertTrue(result.handled)
        self.assertEqual(result.route, ROUTE_JIRA_ISSUE_NOT_FOUND)
        self.assertEqual(result.issue_key, "NIKO-404")
        self.assertEqual(result.deep_context, "")
        self.assertIn("chưa có dữ liệu Jira fixture", result.reply)


if __name__ == "__main__":
    unittest.main()
