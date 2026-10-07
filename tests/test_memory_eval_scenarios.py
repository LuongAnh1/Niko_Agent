import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.decision_model.memory import (
    MEMORY_DISCARD,
    MEMORY_FORGET_MEMORY,
    MEMORY_RETRIEVE,
    MEMORY_SKIP,
    MEMORY_TARGET_FACT,
    MemoryCorrectionDecision,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
)
from niko.chat_gateway import telegram_message_to_gateway
from niko.harness.trace import TraceLogger
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore


class MemoryEvalScenarioTests(unittest.TestCase):
    """Regression nhỏ cho các eval scenario mô tả trong docs/harness."""

    def _message(self, text: str = "hello"):
        return telegram_message_to_gateway(
            {
                "text": text,
                "from": {"id": 123, "first_name": "Tran", "last_name": "Anh"},
                "chat": {"id": 456, "type": "private"},
            }
        )

    def _base_env(self, temp_dir: str) -> dict[str, str]:
        return {
            "NIKO_STATE_DIR": temp_dir,
            "NIKO_RUNTIME_CONFIG_FILE": str(Path(temp_dir) / "config.json"),
            "NIKO_MEMORY_ENABLED": "1",
            "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
            "NIKO_RUNTIME_LOG_ENABLED": "0",
        }

    def test_eval_no_memory_prompt_skips_long_term_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Preference", "Anh thích checklist có mục tiêu rõ ràng")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_SKIP,
                    reason="standalone_small_talk",
                ),
            )

            env = {**self._base_env(temp_dir), "NIKO_MEMORY_GATE_ENABLED": "1"}
            with patch.dict(os.environ, env, clear=False):
                memory = runtime.retrieve_for_deep("haha oke", self._message("haha oke"))

        self.assertNotIn("Relevant semantic facts", memory.text)
        self.assertEqual(memory.to_meta()["gate_decision"], MEMORY_SKIP)
        self.assertEqual(memory.to_meta()["fact_count"], 0)

    def test_eval_direct_memory_question_retrieves_expected_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Docs preference", "Anh thích checklist có mục đích rõ và chia theo phase")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    query="checklist mục đích phase",
                    reason="direct_memory_question",
                ),
            )

            env = {**self._base_env(temp_dir), "NIKO_MEMORY_GATE_ENABLED": "1"}
            with patch.dict(os.environ, env, clear=False):
                memory = runtime.retrieve_for_deep("Anh thích checklist kiểu gì?", self._message())

        self.assertIn("Relevant semantic facts", memory.text)
        self.assertIn("checklist có mục đích rõ", memory.text)
        self.assertEqual(memory.to_meta()["fact_ids"], [fact_id])

    def test_eval_indirect_memory_question_uses_gate_query(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Docs preference", "Anh thích checklist có mục đích rõ và chia theo phase")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    query="checklist mục đích phase",
                    reason="indirect_memory_question",
                ),
            )

            env = {**self._base_env(temp_dir), "NIKO_MEMORY_GATE_ENABLED": "1"}
            with patch.dict(os.environ, env, clear=False):
                memory = runtime.retrieve_for_deep("Lúc viết docs em nên trình bày thế nào?", self._message())

        self.assertIn("checklist có mục đích rõ", memory.text)
        self.assertEqual(memory.to_meta()["fact_ids"], [fact_id])
        self.assertEqual(memory.to_meta()["gate_query"], "checklist mục đích phase")

    def test_eval_retrieval_gate_error_fail_open_uses_raw_prompt(self):
        def broken_decider(_prompt, _message):
            raise RuntimeError("ollama unavailable")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Checklist", "Niko should keep checklist phases explicit")
            runtime = MemoryRuntime(store=store, retrieval_decider=broken_decider)

            env = {**self._base_env(temp_dir), "NIKO_MEMORY_GATE_ENABLED": "1"}
            with patch.dict(os.environ, env, clear=False):
                memory = runtime.retrieve_for_deep("checklist phases", self._message())

        self.assertIn("Niko should keep checklist phases explicit", memory.text)
        self.assertEqual(memory.to_meta()["gate_decision"], MEMORY_RETRIEVE)
        self.assertIn("ollama unavailable", memory.to_meta()["gate_error"])

    def test_eval_write_gate_discard_does_not_create_episode(self):
        message = self._message()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime = MemoryRuntime(
                store=store,
                write_decider=lambda _prompt, _answer, _followups, _message, _route: MemoryWriteDecision(
                    decision=MEMORY_DISCARD,
                    reason="inspect_only",
                ),
            )

            env = {
                **self._base_env(temp_dir),
                "NIKO_MEMORY_WRITE_ENABLED": "1",
                "NIKO_MEMORY_WRITE_GATE_ENABLED": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime.record_deep_episode(
                    "456",
                    "hiện tại em nhớ fact nào?",
                    "Dạ đây là danh sách fact.",
                    message,
                    followups=[],
                    trace_id="turn-write",
                    trace_logger=trace,
                )

            events = trace.read_events()
            episodes = store.recent_episodes()

        self.assertEqual(episodes, [])
        self.assertTrue(
            any(
                event.get("kind") == "memory_write_decision"
                and event.get("data", {}).get("decision") == MEMORY_DISCARD
                for event in events
            )
        )

    def test_eval_correction_forget_deletes_unique_fact_with_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Test correction", "anh thích checklist màu xanh")
            trace = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _message, _context: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist màu xanh",
                    target_type=MEMORY_TARGET_FACT,
                    reason="explicit_forget",
                ),
            )

            env = {
                **self._base_env(temp_dir),
                "NIKO_MEMORY_WRITE_ENABLED": "1",
                "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
            }
            with patch.dict(os.environ, env, clear=False):
                result = runtime.handle_memory_correction(
                    "456",
                    "Niko, quên fact anh thích checklist màu xanh",
                    self._message(),
                    "turn-correction",
                    trace,
                )

            events = trace.read_events()
            remaining_fact_ids = [fact.id for fact in store.list_facts()]

        self.assertTrue(result.handled)
        self.assertEqual(remaining_fact_ids, [])
        self.assertTrue(
            any(
                event.get("kind") == "memory_correction_applied"
                and event.get("data", {}).get("fact_id") == fact_id
                for event in events
            )
        )

    def test_eval_vietnamese_query_with_removed_accents_finds_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Sở thích docs", "Anh thích checklist có mục đích rõ ràng")

            facts = store.search_facts("so thich checklist muc dich", top_k=3)

        self.assertEqual([fact.id for fact in facts], [fact_id])


if __name__ == "__main__":
    unittest.main()
