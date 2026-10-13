import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niko.app import NikoApp, _format_sdlc_reply, create_niko_app
from niko.harness.runtime_log import default_runtime_logger
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore
from niko.sdlc.client import ROUTE_SDLC_APPROVE, ROUTE_SDLC_START, ROUTE_SDLC_START_JIRA, ROUTE_SDLC_STATUS, SdlcResult


class FakeChatGraph:
    def __init__(self) -> None:
        self.calls = []

    def handle_message(self, prompt, gateway_message, deliver_reply, notify_working=None):
        self.calls.append((prompt, gateway_message, deliver_reply, notify_working))
        deliver_reply("ok")
        if notify_working is not None:
            notify_working()
        return "fake_route"


class FakeCorrectionResult:
    def __init__(
        self,
        handled=True,
        reply="memory correction handled",
        route="memory_correction",
    ) -> None:
        self.handled = handled
        self.reply = reply
        self.route = route


class FakeMemoryRuntime:
    def __init__(self, result=None) -> None:
        self.calls = []
        self.result = result if result is not None else FakeCorrectionResult()

    def handle_memory_correction(self, conversation_id, prompt, gateway_message, trace_id, trace_logger):
        self.calls.append((conversation_id, prompt, gateway_message, trace_id, trace_logger))
        return self.result


class FakeSelectableGraph:
    def __init__(self, correction_result=None, route_kind="deep_agent") -> None:
        self.memory_runtime = FakeMemoryRuntime(correction_result)
        self.trace_logger = object()
        self.route_kind = route_kind
        self.finished = []
        self.normal_calls = []
        self.last_two_agent_route = None

    def conversation_id_for(self, gateway_message):
        return "chat-1"

    def start_chat_turn(self, conversation_id, prompt, gateway_message):
        return SimpleNamespace(turn_id="trace-1")

    def decide_two_agent_route(self, prompt, conversation_id, trace_id):
        return SimpleNamespace(kind=self.route_kind, reason="test")

    def handle_single_agent_message(self, prompt, gateway_message, deliver_reply, notify_working, trace_turn):
        self.normal_calls.append(("single", prompt))
        return "single"

    def handle_busy_reply(self, conversation_id, prompt, gateway_message, deliver_reply, trace_turn, route):
        self.normal_calls.append(("busy", prompt))
        return "busy_reply"

    def finish_workflow_reply(self, conversation_id, reply, gateway_message, deliver_reply, *, route, trace_id, meta=None):
        self.finished.append((conversation_id, reply, route, trace_id, meta))
        deliver_reply(reply)
        return route

    def handle_two_agent_message(self, prompt, gateway_message, deliver_reply, notify_working=None, trace_turn=None, route=None):
        self.last_two_agent_route = route
        self.normal_calls.append(("two_agent", prompt))
        return "normal_chat"


class NikoAppTests(unittest.TestCase):
    def test_app_delegates_to_chat_graph_without_changing_callbacks(self):
        graph = FakeChatGraph()
        app = NikoApp(chat_graph=graph)
        delivered = []
        notified = []
        message = object()
        deliver = delivered.append
        notify = lambda: notified.append("typing")

        route = app.handle_message("hello", message, deliver, notify)

        self.assertEqual(route, "fake_route")
        self.assertEqual(delivered, ["ok"])
        self.assertEqual(notified, ["typing"])
        self.assertEqual(len(graph.calls), 1)
        prompt, recorded_message, recorded_deliver, recorded_notify = graph.calls[0]
        self.assertEqual(prompt, "hello")
        self.assertIs(recorded_message, message)
        self.assertIs(recorded_deliver, deliver)
        self.assertIs(recorded_notify, notify)

    def test_create_niko_app_keeps_test_graph_injection(self):
        graph = FakeChatGraph()
        app = create_niko_app(chat_graph=graph)

        self.assertIs(app.chat_graph, graph)

    def test_app_builds_chat_graph_with_injected_dependencies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir)
            store = MemoryStore(state_path / "memory.sqlite3")
            memory_runtime = MemoryRuntime(store=store)
            trace_logger = TraceLogger(state_path / "traces", enabled=False)

            app = create_niko_app(
                memory_store=store,
                memory_runtime=memory_runtime,
                trace_logger=trace_logger,
            )

            self.assertIs(app.chat_graph.memory_store, store)
            self.assertIs(app.chat_graph.memory_runtime, memory_runtime)
            self.assertIs(app.chat_graph.trace_logger, trace_logger)

    def test_app_rejects_graph_and_dependency_injection_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")

            with self.assertRaises(ValueError):
                NikoApp(chat_graph=FakeChatGraph(), memory_store=store)

    def test_app_runs_memory_correction_before_jira_selection(self):
        graph = FakeSelectableGraph()
        app = NikoApp(chat_graph=graph)
        message = object()
        delivered = []

        with patch.dict(
            "os.environ",
            {"NIKO_AGENT_MODE": "two_agent", "NIKO_JIRA_TOOLS_ENABLED": "1", "NIKO_RUNTIME_LOG_ENABLED": "0"},
            clear=False,
        ), patch("niko.app.JiraIssueAnalysisWorkflow") as jira_workflow:
            route = app.handle_message("phan tich NIKO-101 giup anh", message, delivered.append)

        self.assertEqual(route, "memory_correction")
        self.assertEqual(delivered, ["memory correction handled"])
        self.assertEqual(len(graph.memory_runtime.calls), 1)
        self.assertEqual(graph.normal_calls, [])
        jira_workflow.assert_not_called()

    def test_app_busy_route_skips_memory_correction(self):
        graph = FakeSelectableGraph(route_kind="busy_reply")
        app = NikoApp(chat_graph=graph)
        delivered = []

        with patch.dict("os.environ", {"NIKO_AGENT_MODE": "two_agent", "NIKO_RUNTIME_LOG_ENABLED": "0"}, clear=False):
            route = app.handle_message("fact #8 nhe", object(), delivered.append)

        self.assertEqual(route, "busy_reply")
        self.assertEqual(graph.memory_runtime.calls, [])
        self.assertEqual(graph.normal_calls, [("busy", "fact #8 nhe")])

    def test_app_continues_to_normal_chat_when_memory_correction_is_not_handled(self):
        graph = FakeSelectableGraph(correction_result=FakeCorrectionResult(handled=False, reply="", route=""))
        app = NikoApp(chat_graph=graph)

        with patch.dict(
            "os.environ",
            {"NIKO_AGENT_MODE": "two_agent", "NIKO_JIRA_TOOLS_ENABLED": "0", "NIKO_RUNTIME_LOG_ENABLED": "0"},
            clear=False,
        ):
            route = app.handle_message("hello", object(), lambda _reply: None)

        self.assertEqual(route, "normal_chat")
        self.assertEqual(len(graph.memory_runtime.calls), 1)
        self.assertEqual(graph.normal_calls, [("two_agent", "hello")])
        self.assertIsNotNone(graph.last_two_agent_route)

    def test_app_routes_enabled_sdlc_prompt_before_legacy_jira(self):
        graph = FakeSelectableGraph(correction_result=FakeCorrectionResult(handled=False, reply="", route=""))
        app = NikoApp(chat_graph=graph)
        message = SimpleNamespace(user=SimpleNamespace(key="telegram:1"))
        delivered = []
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_START,
            run_id="run_20261012_120000_abcd1234",
            source_ref="eTour UC1",
            current_phase="phase_1",
            status="checkpoint_pending",
            summary_for_user="Da tao requirement packet cho UC1.",
            pending_checkpoint={"title": "Duyet requirement packet"},
            allowed_next_actions=["approve", "revise"],
            latest_artifacts={"requirement_packet": "runtime/path.json"},
        )

        with patch.dict(
            "os.environ",
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_JIRA_TOOLS_ENABLED": "1",
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        ), patch("niko.app.SdlcClient") as client_class, patch("niko.app.JiraIssueAnalysisWorkflow") as jira_workflow:
            client_class.return_value.start_or_get_run.return_value = result
            route = app.handle_message("Niko thuc hien eTour UC1", message, delivered.append)

        self.assertEqual(route, ROUTE_SDLC_START)
        self.assertIn("SDLC_Harness", delivered[0])
        self.assertIn("run_20261012_120000_abcd1234", delivered[0])
        client_class.return_value.start_or_get_run.assert_called_once_with(
            dataset="eTour",
            use_case="UC1",
            requested_by="telegram:1",
        )
        jira_workflow.assert_not_called()
        self.assertEqual(graph.normal_calls, [])

    def test_app_routes_sdlc_status_prompt(self):
        graph = FakeSelectableGraph(correction_result=FakeCorrectionResult(handled=False, reply="", route=""))
        app = NikoApp(chat_graph=graph)
        message = SimpleNamespace(user=SimpleNamespace(key="telegram:1"))
        delivered = []
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_STATUS,
            run_id="run_20261012_120000_abcd1234",
            current_phase="phase_1",
            status="checkpoint_pending",
            summary_for_user="Dang cho anh duyet requirement packet.",
        )

        with patch.dict(
            "os.environ",
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        ), patch("niko.app.SdlcClient") as client_class:
            client_class.return_value.get_status.return_value = result
            route = app.handle_message(
                "run_20261012_120000_abcd1234 den dau roi em?",
                message,
                delivered.append,
            )

        self.assertEqual(route, ROUTE_SDLC_STATUS)
        self.assertIn("checkpoint_pending", delivered[0])
        client_class.return_value.get_status.assert_called_once_with(run_id="run_20261012_120000_abcd1234")
        self.assertEqual(graph.normal_calls, [])

    def test_app_routes_sdlc_approve_prompt(self):
        graph = FakeSelectableGraph(correction_result=FakeCorrectionResult(handled=False, reply="", route=""))
        app = NikoApp(chat_graph=graph)
        message = SimpleNamespace(user=SimpleNamespace(key="telegram:1"))
        delivered = []
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_APPROVE,
            run_id="run_20261012_120000_abcd1234",
            current_phase="design_review",
            status="waiting_design_approval",
            summary_for_user="Da duyet requirement va tao design checkpoint V2.",
            pending_checkpoint={"type": "design_approval", "artifact_ref": "design_artifact_v2"},
            allowed_next_actions=["approve_checkpoint", "request_change", "cancel_run", "get_status"],
            latest_artifacts={"design_artifact": "path", "design_quality_report": "path"},
            quality_reports={
                "design_traceability": {
                    "summary": "khong phat hien missing link class/sequence.",
                    "versions": ["v1", "v2"],
                }
            },
        )

        with patch.dict(
            "os.environ",
            {
                "NIKO_AGENT_MODE": "two_agent",
                "NIKO_SDLC_HARNESS_ENABLED": "1",
                "NIKO_RUNTIME_LOG_ENABLED": "0",
            },
            clear=False,
        ), patch("niko.app.SdlcClient") as client_class:
            client_class.return_value.approve_checkpoint.return_value = result
            route = app.handle_message(
                "Niko, duyet run_20261012_120000_abcd1234 giup anh",
                message,
                delivered.append,
            )

        self.assertEqual(route, ROUTE_SDLC_APPROVE)
        self.assertIn("design checkpoint", delivered[0])
        self.assertIn("repair iterations: 1", delivered[0])
        self.assertIn("design_artifact_v2", delivered[0])
        self.assertNotIn("muốn sửa", delivered[0])
        client_class.return_value.approve_checkpoint.assert_called_once_with(run_id="run_20261012_120000_abcd1234")
        self.assertEqual(graph.normal_calls, [])

    def test_sdlc_reply_formats_checkpoint_for_telegram(self):
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_START_JIRA,
            run_id="run_20261012_170506_b0909cb6",
            source_ref="jira_issue:SDLC-1",
            current_phase="waiting_requirement_approval",
            status="waiting_requirement_approval",
            summary_for_user=(
                "Đã chuẩn hóa jira SDLC-1 thành formal use case và requirement packet v1 "
                "bằng Requirement Refiner, validator đã pass, và sync status=synced. "
                "Run đang chờ anh duyệt requirement trước khi chuyển sang design."
            ),
            allowed_next_actions=["approve_checkpoint", "request_change", "cancel_run", "get_status"],
            latest_artifacts={"requirement_packet": "path", "trace_graph": "path"},
            last_sync={
                "mode": "real",
                "status": "synced",
                "targets": [
                    {"tool": "jira", "status": "synced", "url": "https://example.atlassian.net/browse/SDLC-1"},
                    {"tool": "confluence", "status": "synced", "url": "https://example.atlassian.net/wiki/x/abc"},
                ],
            },
        )

        reply = _format_sdlc_reply(result)

        self.assertIn("Jira issue `SDLC-1`", reply)
        self.assertIn("đang chờ anh duyệt requirement", reply)
        self.assertIn("Đã đồng bộ:", reply)
        self.assertIn("https://example.atlassian.net/browse/SDLC-1", reply)
        self.assertIn("Việc tiếp theo:", reply)
        self.assertNotIn("approve_checkpoint", reply)
        self.assertNotIn("muốn sửa", reply)

    def test_sdlc_reply_formats_design_checkpoint_static_approve_only(self):
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_STATUS,
            run_id="run_20261013_052040_47cd92fc",
            source_ref="jira_issue:SDLC-1",
            current_phase="design_review",
            status="waiting_design_approval",
            summary_for_user="Da tao design checkpoint V2.",
            pending_checkpoint={"type": "design_approval", "artifact_ref": "design_artifact_v2"},
            allowed_next_actions=["approve_checkpoint", "request_change", "cancel_run", "get_status"],
            latest_artifacts={"design_artifact": "path", "design_quality_report": "path"},
            quality_reports={
                "design_traceability": {
                    "summary": "khong phat hien missing link class/sequence.",
                    "versions": ["v1", "v2"],
                }
            },
            last_sync={
                "mode": "real",
                "status": "synced",
                "targets": [
                    {"tool": "jira", "status": "synced", "url": "https://example.atlassian.net/browse/SDLC-1"},
                    {"tool": "confluence", "status": "synced", "url": "https://example.atlassian.net/wiki/x/design"},
                ],
            },
        )

        reply = _format_sdlc_reply(result)

        self.assertIn("đang chờ anh review design checkpoint", reply)
        self.assertIn("design_artifact_v2", reply)
        self.assertIn("repair iterations: 1", reply)
        self.assertIn("https://example.atlassian.net/wiki/x/design", reply)
        self.assertNotIn("request_change", reply)
        self.assertNotIn("muốn sửa", reply)

    def test_sdlc_reply_formats_blocked_with_last_error_and_partial_sync(self):
        result = SdlcResult(
            ok=True,
            action=ROUTE_SDLC_STATUS,
            run_id="run_20261013_052040_47cd92fc",
            source_ref="jira_issue:SDLC-1",
            current_phase="design_review",
            status="blocked",
            summary_for_user="Phase 2 bị chặn vì requirement chưa đủ rõ.",
            allowed_next_actions=["get_status", "request_change", "cancel_run"],
            latest_artifacts={"design_artifact": "path", "design_quality_report": "path"},
            last_error={
                "error_code": "requirement_traceability_blocked",
                "message": "Requirement packet did not contain traceable requirement items.",
            },
            last_sync={
                "mode": "real",
                "status": "partial_failed",
                "targets": [
                    {"tool": "jira", "status": "synced", "url": "https://example.atlassian.net/browse/SDLC-1"},
                    {"tool": "confluence", "status": "failed", "error": "API timeout while updating page"},
                ],
            },
        )

        reply = _format_sdlc_reply(result)

        self.assertIn("đang bị chặn", reply)
        self.assertIn("Đồng bộ một phần:", reply)
        self.assertIn("Confluence: failed", reply)
        self.assertIn("requirement_traceability_blocked", reply)
        self.assertIn("Requirement packet did not contain traceable requirement items", reply)
        self.assertNotIn("Nếu ổn, nhắn em duyệt", reply)

    def test_app_logs_memory_correction_workflow_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            graph = FakeSelectableGraph()
            app = NikoApp(chat_graph=graph)

            with patch.dict(
                "os.environ",
                {
                    "NIKO_AGENT_MODE": "two_agent",
                    "NIKO_JIRA_TOOLS_ENABLED": "0",
                    "NIKO_RUNTIME_LOG_ENABLED": "1",
                    "NIKO_STATE_DIR": temp_dir,
                },
                clear=False,
            ):
                route = app.handle_message("quen fact checklist", object(), lambda _reply: None)
                events = default_runtime_logger().read_events(source="niko_app")

        self.assertEqual(route, "memory_correction")
        event = next(log for log in events if log["event"] == "workflow_selected")
        self.assertEqual(event["data"]["workflow"], "memory_correction")
        self.assertEqual(event["data"]["route"], "memory_correction")
        self.assertEqual(event["data"]["conversation_id"], "chat-1")
        self.assertEqual(event["data"]["trace_id"], "trace-1")

    def test_app_logs_normal_chat_workflow_route(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            graph = FakeSelectableGraph(
                correction_result=FakeCorrectionResult(handled=False, reply="", route=""),
                route_kind="fast_agent",
            )
            app = NikoApp(chat_graph=graph)

            with patch.dict(
                "os.environ",
                {
                    "NIKO_AGENT_MODE": "two_agent",
                    "NIKO_JIRA_TOOLS_ENABLED": "0",
                    "NIKO_RUNTIME_LOG_ENABLED": "1",
                    "NIKO_STATE_DIR": temp_dir,
                },
                clear=False,
            ):
                route = app.handle_message("hello", object(), lambda _reply: None)
                events = default_runtime_logger().read_events(source="niko_app")

        self.assertEqual(route, "normal_chat")
        event = next(log for log in events if log["event"] == "workflow_selected")
        self.assertEqual(event["data"]["workflow"], "normal_chat")
        self.assertEqual(event["data"]["route"], "fast_agent")
        self.assertEqual(event["data"]["conversation_id"], "chat-1")
        self.assertEqual(event["data"]["trace_id"], "trace-1")


if __name__ == "__main__":
    unittest.main()
