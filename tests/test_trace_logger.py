import tempfile
import unittest
from pathlib import Path

from niko.harness.trace import TraceLogger


class TraceLoggerTests(unittest.TestCase):
    def test_writes_turn_events_as_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)

            turn = logger.turn_start("chat-1", "telegram:123", "hello")
            logger.event(turn.turn_id, "route_decision", {"route": "local_reply"})
            logger.turn_end(turn.turn_id, reply="Da anh", status="ok")

            events = logger.read_events()

        event_types = {event["type"] for event in events}
        event_kinds = {event.get("kind") for event in events}
        self.assertIn("turn_start", event_types)
        self.assertIn("turn_end", event_types)
        self.assertIn("route_decision", event_kinds)


if __name__ == "__main__":
    unittest.main()
