import tempfile
import unittest
from pathlib import Path

from niko.harness.runtime_log import RuntimeEventLogger


class RuntimeEventLoggerTests(unittest.TestCase):
    def test_runtime_log_masks_sensitive_data_and_filters_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = RuntimeEventLogger(Path(temp_dir), enabled=True)

            logger.event(
                "telegram_bot",
                "config_loaded",
                "loaded",
                data={"bot_token": "123456:secret", "chat_id": -100},
            )
            logger.event("chat_graph", "route_decision", "route=fast_agent")

            telegram_events = logger.read_events(source="telegram_bot")

            self.assertEqual(len(telegram_events), 1)
            self.assertEqual(telegram_events[0]["data"]["bot_token"], "***")
            self.assertEqual(telegram_events[0]["data"]["chat_id"], -100)
            self.assertEqual(logger.read_events(limit=1)[0]["source"], "chat_graph")


if __name__ == "__main__":
    unittest.main()
