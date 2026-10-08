import tempfile
import unittest
from pathlib import Path

from niko.harness.trace import TraceLogger
from niko.loop import (
    LoopDecision,
    LoopRuntime,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    TraceLoopObserver,
)


def echo_tool(args, context):
    return ToolResult(ok=True, text=f"echo:{args.get('value', '')}", data={"conversation_id": context.conversation_id})


class RecordingObserver:
    def __init__(self):
        self.events = []

    def event(self, kind, data=None):
        self.events.append((kind, data or {}))


class LoopRuntimeTests(unittest.TestCase):
    def test_registry_registers_schema_and_rejects_duplicate_names(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="echo",
                description="Echo input",
                input_schema={"type": "object"},
                handler=echo_tool,
            )
        )

        self.assertEqual(registry.schemas()[0]["name"], "echo")
        with self.assertRaises(ValueError):
            registry.register(
                Tool(
                    name="echo",
                    description="Duplicate",
                    input_schema={},
                    handler=echo_tool,
                )
            )

    def test_registry_execute_unknown_tool_returns_error_result(self):
        result = ToolRegistry().execute("missing", {}, ToolContext())

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unknown_tool")

    def test_registry_converts_tool_exception_to_error_result(self):
        def broken_tool(args, context):
            raise RuntimeError("boom")

        registry = ToolRegistry(
            [
                Tool(
                    name="broken",
                    description="Broken tool",
                    input_schema={},
                    handler=broken_tool,
                )
            ]
        )

        result = registry.execute("broken", {}, ToolContext())

        self.assertFalse(result.ok)
        self.assertIn("boom", result.error)

    def test_runtime_returns_final_without_calling_tools(self):
        def controller(prompt, context, history, tools):
            return LoopDecision.final("done", reason="enough")

        runtime = LoopRuntime(controller, ToolRegistry([self._echo_tool()]))

        result = runtime.run("hello", ToolContext(conversation_id="chat-1"))

        self.assertEqual(result.reply, "done")
        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.iterations, 1)
        self.assertFalse(result.limit_reached)

    def test_runtime_calls_tool_then_returns_final(self):
        def controller(prompt, context, history, tools):
            if not history:
                return LoopDecision.tool("echo", {"value": prompt}, reason="need echo")
            return LoopDecision.final(f"observed {history[-1]['text']}", reason="tool observed")

        runtime = LoopRuntime(controller, ToolRegistry([self._echo_tool()]))

        result = runtime.run("hello", ToolContext(conversation_id="chat-1"))

        self.assertEqual(result.reply, "observed echo:hello")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]["tool_name"], "echo")
        self.assertEqual(result.iterations, 2)

    def test_runtime_stops_when_max_iterations_reached(self):
        def controller(prompt, context, history, tools):
            return LoopDecision.tool("echo", {"value": len(history)}, reason="keep going")

        runtime = LoopRuntime(controller, ToolRegistry([self._echo_tool()]), max_iterations=2)

        result = runtime.run("hello", ToolContext())

        self.assertTrue(result.limit_reached)
        self.assertEqual(result.iterations, 2)
        self.assertEqual(len(result.tool_calls), 2)

    def test_observer_receives_loop_events(self):
        observer = RecordingObserver()

        def controller(prompt, context, history, tools):
            if not history:
                return LoopDecision.tool("echo", {"value": "x"})
            return LoopDecision.final("done")

        runtime = LoopRuntime(controller, ToolRegistry([self._echo_tool()]), observer=observer)

        runtime.run("hello", ToolContext())

        kinds = [kind for kind, _ in observer.events]
        self.assertEqual(kinds[0], "loop_started")
        self.assertIn("loop_decision", kinds)
        self.assertIn("loop_tool_call_started", kinds)
        self.assertIn("loop_tool_call_finished", kinds)
        self.assertIn("loop_final_answer", kinds)

    def test_trace_observer_writes_loop_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            turn = trace_logger.turn_start("chat-1", "telegram:1", "hello")
            observer = TraceLoopObserver(trace_logger, trace_id=turn.turn_id)

            def controller(prompt, context, history, tools):
                return LoopDecision.final("done")

            runtime = LoopRuntime(controller, ToolRegistry(), observer=observer)
            runtime.run("hello", ToolContext(trace_id=turn.turn_id))

            events = trace_logger.read_events()

        kinds = {event.get("kind") for event in events}
        self.assertIn("loop_started", kinds)
        self.assertIn("loop_final_answer", kinds)

    @staticmethod
    def _echo_tool():
        return Tool(
            name="echo",
            description="Echo input",
            input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
            handler=echo_tool,
        )


if __name__ == "__main__":
    unittest.main()

