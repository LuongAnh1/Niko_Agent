import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
