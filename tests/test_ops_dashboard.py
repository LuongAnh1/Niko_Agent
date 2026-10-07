import json
import os
import tempfile
import threading
import time
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from niko.harness.runtime_log import RuntimeEventLogger
from niko.harness.trace import TraceLogger
from niko.memory.store import MemoryStore
from niko.ops.dashboard import TelegramBotProcessManager, create_server
from bots.telegram.instance_guard import TelegramBotInstanceInfo
from bots.decision_model.memory import MEMORY_SEMANTIC_FACT, MemoryCandidateDecision

TRANSIENT_LOCAL_HTTP_ERRORS = (ConnectionAbortedError, ConnectionResetError, RemoteDisconnected, URLError)


class OpsDashboardTests(unittest.TestCase):
    def test_telegram_manager_reports_external_instance_and_blocks_start(self):
        external = TelegramBotInstanceInfo(pid=4242, command="python -m bots.telegram.bot", lock_path="lock")
        manager = TelegramBotProcessManager(runtime_logger=RuntimeEventLogger(enabled=False))

        with patch("niko.ops.bots.read_active_instance", return_value=external), patch(
            "niko.ops.bots.subprocess.Popen"
        ) as popen:
            status = manager.telegram_bot()
            started = manager.start()

        self.assertTrue(status["external"])
        self.assertEqual(status["health"], "external")
        self.assertEqual(started["pid"], 4242)
        popen.assert_not_called()

    def test_telegram_manager_starts_with_clean_runtime_env(self):
        manager = TelegramBotProcessManager(runtime_logger=RuntimeEventLogger(enabled=False))

        with patch("niko.ops.bots.read_active_instance", return_value=None), patch(
            "niko.ops.bots.runtime_subprocess_env",
            return_value={"NIKO_RUNTIME_CONFIG_FILE": "niko/.runtime/config.json"},
        ), patch("niko.ops.bots.subprocess.Popen") as popen:
            process = popen.return_value
            process.poll.return_value = None
            process.pid = 4321

            manager.start()

        self.assertEqual(popen.call_args.kwargs["env"], {"NIKO_RUNTIME_CONFIG_FILE": "niko/.runtime/config.json"})

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

    def test_memory_consolidation_api_previews_and_runs_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat("chat-1", "user", "Ghi nhớ rằng anh thích memory rõ ràng", source="test")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            with patch(
                "niko.memory.consolidation.classify_memory_candidate",
                return_value=MemoryCandidateDecision(decision=MEMORY_SEMANTIC_FACT, reason="explicit"),
            ):
                server = create_server("127.0.0.1", 0, memory_store=store, trace_logger=trace_logger)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{server.server_port}"
                try:
                    preview = self._json_get(f"{base_url}/api/memory/consolidation")
                    self.assertEqual(preview["batch"]["rows_read"], 1)
                    self.assertEqual(preview["candidates"][0]["kind_hint"], MEMORY_SEMANTIC_FACT)

                    result = self._json_post(f"{base_url}/api/memory/consolidation/run", {})["result"]
                    self.assertEqual(result["status"], "stored")
                    self.assertEqual(result["marked_count"], 1)

                    memory = self._json_get(f"{base_url}/api/memory")["memory"]
                    self.assertEqual(memory["counts"]["facts"], 1)
                    self.assertEqual(memory["facts"][0]["source"], "consolidation")
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

    def test_config_api_masks_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            with patch.dict(os.environ, {"NIKO_RUNTIME_CONFIG_FILE": str(config_path)}, clear=False):
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
                server = create_server("127.0.0.1", 0, memory_store=store, trace_logger=trace_logger)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base_url = f"http://127.0.0.1:{server.server_port}"
                try:
                    updated = self._json_post(
                        f"{base_url}/api/config",
                        {"values": {"TELEGRAM_BOT_TOKEN": "123456:secret-token"}},
                    )["config"]

                    self.assertEqual(updated["overrides"]["TELEGRAM_BOT_TOKEN"], "********")
                    telegram = next(section for section in updated["sections"] if section["id"] == "telegram")
                    token_field = next(field for field in telegram["fields"] if field["name"] == "TELEGRAM_BOT_TOKEN")
                    self.assertEqual(token_field["value"], "********")
                    self.assertNotIn("secret-token", json.dumps(updated))
                    self.assertIn("secret-token", config_path.read_text(encoding="utf-8"))

                    memory = next(section for section in updated["sections"] if section["id"] == "memory")
                    retrieval_gate = next(
                        field for field in memory["fields"] if field["name"] == "NIKO_MEMORY_GATE_ENABLED"
                    )
                    self.assertIn("Deep", retrieval_gate["help"])

                    decision = next(section for section in updated["sections"] if section["id"] == "decision")
                    keep_alive = next(
                        field for field in decision["fields"] if field["name"] == "NIKO_DECISION_MODEL_KEEP_ALIVE"
                    )
                    self.assertIn("RAM/VRAM", keep_alive["help"])
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_bots_telegram_api_uses_injected_manager(self):
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
                started = self._json_post(f"{base_url}/api/bots/telegram/start", {})["bot"]
                self.assertTrue(started["running"])

                stopped = self._json_post(f"{base_url}/api/bots/telegram/stop", {})["bot"]
                self.assertFalse(stopped["running"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_bots_api_returns_runtime_logs_and_actions(self):
        class FakeBotManager:
            def __init__(self):
                self.started = False
                self.warmed = False

            def status(self):
                return {"id": "telegram", "managed": self.started, "running": self.started, "pid": 456}

            def start(self):
                self.started = True
                return self.status()

            def stop(self):
                self.started = False
                return self.status()

            def restart(self):
                self.started = True
                return self.status()

            def warmup_decision(self):
                self.warmed = True
                return {"id": "decision", "running": True, "health": "ready"}

            def stop_decision(self):
                self.warmed = False
                return {"id": "decision", "running": False, "health": "stopped"}

            def bots(self):
                return [
                    {"id": "telegram", "name": "Telegram Bot", "running": self.started, "health": "running"},
                    {
                        "id": "decision",
                        "name": "Decision Model",
                        "running": True,
                        "health": "ready",
                        "actions": ["warmup", "stop"],
                    },
                ]

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime_logger = RuntimeEventLogger(Path(temp_dir) / "logs", enabled=True)
            runtime_logger.event("telegram_bot", "telegram_message_processed", "route=fast_agent")
            manager = FakeBotManager()
            server = create_server(
                "127.0.0.1",
                0,
                memory_store=store,
                trace_logger=trace_logger,
                bot_manager=manager,
                runtime_logger=runtime_logger,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                bots_payload = self._json_get(f"{base_url}/api/bots")
                self.assertEqual(len(bots_payload["bots"]), 2)
                self.assertEqual(bots_payload["logs"][0]["event"], "telegram_message_processed")

                started = self._json_post(f"{base_url}/api/bots/telegram/start", {})["bot"]
                self.assertTrue(started["running"])

                warmed = self._json_post(f"{base_url}/api/bots/decision/warmup", {})["bot"]
                self.assertEqual(warmed["health"], "ready")

                stopped = self._json_post(f"{base_url}/api/bots/decision/stop", {})["bot"]
                self.assertEqual(stopped["health"], "stopped")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def _json_get(self, url: str) -> dict:
        return self._json_request(url)

    def _json_post(self, url: str, payload: dict) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._json_request(request)

    def _json_delete(self, url: str) -> dict:
        request = Request(url, method="DELETE")
        return self._json_request(request)

    def _json_request(self, request) -> dict:
        for attempt in range(3):
            try:
                with urlopen(request, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))
            except TRANSIENT_LOCAL_HTTP_ERRORS as exc:
                if isinstance(exc, HTTPError):
                    raise
                if attempt == 2:
                    raise
                time.sleep(0.05)
        raise AssertionError("unreachable")


if __name__ == "__main__":
    unittest.main()
