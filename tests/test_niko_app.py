import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from niko.app import NikoApp, create_niko_app
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore


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

        with patch.dict("os.environ", {"NIKO_AGENT_MODE": "two_agent", "NIKO_JIRA_TOOLS_ENABLED": "1"}, clear=False), patch(
            "niko.app.JiraIssueAnalysisWorkflow"
        ) as jira_workflow:
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

        with patch.dict("os.environ", {"NIKO_AGENT_MODE": "two_agent"}, clear=False):
            route = app.handle_message("fact #8 nhe", object(), delivered.append)

        self.assertEqual(route, "busy_reply")
        self.assertEqual(graph.memory_runtime.calls, [])
        self.assertEqual(graph.normal_calls, [("busy", "fact #8 nhe")])

    def test_app_continues_to_normal_chat_when_memory_correction_is_not_handled(self):
        graph = FakeSelectableGraph(correction_result=FakeCorrectionResult(handled=False, reply="", route=""))
        app = NikoApp(chat_graph=graph)

        with patch.dict("os.environ", {"NIKO_AGENT_MODE": "two_agent", "NIKO_JIRA_TOOLS_ENABLED": "0"}, clear=False):
            route = app.handle_message("hello", object(), lambda _reply: None)

        self.assertEqual(route, "normal_chat")
        self.assertEqual(len(graph.memory_runtime.calls), 1)
        self.assertEqual(graph.normal_calls, [("two_agent", "hello")])
        self.assertIsNotNone(graph.last_two_agent_route)


if __name__ == "__main__":
    unittest.main()
