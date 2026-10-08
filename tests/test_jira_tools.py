import unittest

from niko.loop import LoopDecision, LoopRuntime, ToolContext, ToolRegistry
from niko.tools.jira import JiraFixtureStore, build_jira_issue_tools, extract_issue_keys


class JiraIssueToolsTests(unittest.TestCase):
    def test_extract_issue_keys_keeps_order_and_removes_duplicates(self):
        keys = extract_issue_keys("xem NIKO-101 rồi niko-102, sau đó NIKO-101")

        self.assertEqual(keys, ["NIKO-101", "NIKO-102"])

    def test_parse_issue_key_tool_returns_first_key_and_all_keys(self):
        registry = self._registry()

        result = registry.execute(
            "parse_issue_key",
            {"prompt": "phân tích NIKO-101 và NIKO-102"},
            ToolContext(),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["issue_key"], "NIKO-101")
        self.assertEqual(result.data["issue_keys"], ["NIKO-101", "NIKO-102"])

    def test_parse_issue_key_tool_fails_closed_when_missing_key(self):
        registry = self._registry()

        result = registry.execute("parse_issue_key", {"prompt": "không có mã issue"}, ToolContext())

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "issue_key_not_found")

    def test_fetch_issue_returns_core_fields_and_evidence(self):
        registry = self._registry()

        result = registry.execute("fetch_jira_issue", {"issue_key": "niko-101"}, ToolContext())

        self.assertTrue(result.ok)
        self.assertEqual(result.data["issue"]["key"], "NIKO-101")
        self.assertEqual(result.data["source"], "jira_fixture:demo_issues.json")
        self.assertEqual(result.data["evidence"][0]["issue_key"], "NIKO-101")
        self.assertNotIn("comments", result.data["issue"])
        self.assertNotIn("changelog", result.data["issue"])

    def test_fetch_comments_and_changelog_are_read_only(self):
        registry = self._registry()
        schemas = {schema["name"]: schema for schema in registry.schemas()}

        comments = registry.execute("fetch_jira_comments", {"issue_key": "NIKO-101", "limit": 1}, ToolContext())
        changelog = registry.execute("fetch_jira_changelog", {"issue_key": "NIKO-101"}, ToolContext())

        self.assertTrue(comments.ok)
        self.assertEqual(len(comments.data["comments"]), 1)
        self.assertTrue(changelog.ok)
        self.assertGreaterEqual(len(changelog.data["changelog"]), 1)
        self.assertFalse(schemas["parse_issue_key"]["mutates_state"])
        self.assertFalse(schemas["fetch_jira_issue"]["mutates_state"])
        self.assertFalse(schemas["fetch_jira_comments"]["mutates_state"])
        self.assertFalse(schemas["fetch_jira_changelog"]["mutates_state"])

    def test_fetch_missing_issue_returns_tool_error_without_crashing(self):
        registry = self._registry()

        result = registry.execute("fetch_jira_issue", {"issue_key": "NIKO-404"}, ToolContext())

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "issue_not_found")
        self.assertEqual(result.data["issue_key"], "NIKO-404")

    def test_loop_runtime_can_parse_then_fetch_issue(self):
        registry = self._registry()

        def controller(prompt, context, history, tools):
            tool_results = [item for item in history if item.get("type") == "tool_result"]
            if not history:
                return LoopDecision.tool("parse_issue_key", {"prompt": prompt}, reason="find_issue_key")
            if len(tool_results) == 1:
                return LoopDecision.tool(
                    "fetch_jira_issue",
                    {"issue_key": tool_results[-1]["data"]["issue_key"]},
                    reason="fetch_issue",
                )
            return LoopDecision.final(tool_results[-1]["data"]["issue"]["summary"])

        result = LoopRuntime(controller, registry, max_iterations=5).run(
            "phân tích NIKO-101 giúp anh",
            ToolContext(trace_id="trace-1"),
        )

        self.assertIn("Memory correction loop", result.reply)
        self.assertEqual([call["tool_name"] for call in result.tool_calls], ["parse_issue_key", "fetch_jira_issue"])
        self.assertFalse(any(call["mutates_state"] for call in result.tool_calls))

    @staticmethod
    def _registry() -> ToolRegistry:
        store = JiraFixtureStore()
        return ToolRegistry(build_jira_issue_tools(store_provider=lambda: store))


if __name__ == "__main__":
    unittest.main()
