import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from niko.harness.trace import TraceLogger
from niko.memory.store import MemoryStore
from niko.ops.dashboard import create_server


class OpsDashboardTests(unittest.TestCase):
    def test_snapshot_add_and_delete_fact_api(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            server = create_server("127.0.0.1", 0, memory_store=store, trace_logger=trace_logger)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                snapshot = self._json_get(f"{base_url}/api/snapshot")
                self.assertEqual(snapshot["memory"]["counts"]["facts"], 0)

                created = self._json_post(
                    f"{base_url}/api/memory/facts",
                    {"subject": "Niko", "content": "Ops can manage semantic memory"},
                )
                fact_id = created["id"]

                memory = self._json_get(f"{base_url}/api/memory")
                self.assertEqual(memory["memory"]["counts"]["facts"], 1)
                self.assertEqual(memory["memory"]["facts"][0]["id"], fact_id)

                deleted = self._json_delete(f"{base_url}/api/memory/facts/{fact_id}")
                self.assertTrue(deleted["deleted"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_config_api_updates_and_resets_runtime_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            with patch.dict(os.environ, {"NIKO_RUNTIME_CONFIG_FILE": str(config_path)}, clear=False):
                server = create_server("127.0.0.1", 0, memory_store=store, trace_logger=trace_logger)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{server.server_port}"
                try:
                    config = self._json_get(f"{base_url}/api/config")["config"]
                    self.assertEqual(config["overrides"], {})

                    updated = self._json_post(
                        f"{base_url}/api/config",
                        {"values": {"NIKO_DECISION_MODEL_ENABLED": False, "NIKO_MEMORY_TOP_K": 6}},
                    )["config"]
                    self.assertEqual(updated["overrides"]["NIKO_DECISION_MODEL_ENABLED"], "0")
                    self.assertEqual(updated["overrides"]["NIKO_MEMORY_TOP_K"], "6")

                    reset = self._json_post(f"{base_url}/api/config/reset", {"section": "decision"})["config"]
                    self.assertNotIn("NIKO_DECISION_MODEL_ENABLED", reset["overrides"])
                    self.assertEqual(reset["overrides"]["NIKO_MEMORY_TOP_K"], "6")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_runtime_bot_api_uses_injected_manager(self):
        class FakeBotManager:
            def __init__(self):
                self.running = False

            def status(self):
                return {"managed": self.running, "running": self.running, "pid": 123 if self.running else None}

            def start(self):
                self.running = True
                return self.status()

            def stop(self):
                self.running = False
                return self.status()

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            manager = FakeBotManager()
            server = create_server("127.0.0.1", 0, memory_store=store, trace_logger=trace_logger, bot_manager=manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                started = self._json_post(f"{base_url}/api/runtime/bot/start", {})["bot"]
                self.assertTrue(started["running"])

                stopped = self._json_post(f"{base_url}/api/runtime/bot/stop", {})["bot"]
                self.assertFalse(stopped["running"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def _json_get(self, url: str) -> dict:
        with urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _json_post(self, url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _json_delete(self, url: str) -> dict:
        request = Request(url, method="DELETE")
        with urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
