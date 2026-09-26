import json
import tempfile
import threading
import unittest
from pathlib import Path
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
