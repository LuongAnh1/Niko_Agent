import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niko.chat_gateway import telegram_message_to_gateway
from niko.harness.trace import TraceLogger
from niko.memory.context import retrieve_memory_context
from niko.memory.store import MemoryStore
from niko.runtime import call_deep_agent


class MemoryStoreTests(unittest.TestCase):
    def test_add_search_and_delete_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Niko", "Niko harness uses SQLite memory", source="test")

            facts = store.search_facts("SQLite memory")

            self.assertEqual(facts[0].id, fact_id)
            self.assertEqual(facts[0].subject, "Niko")
            self.assertTrue(store.delete_fact(fact_id))
            self.assertEqual(store.search_facts("SQLite memory"), [])

    def test_add_and_search_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            episode_id = store.add_episode("User asked Niko to analyze Jira incidents", source="test")

            episodes = store.search_episodes("Jira incidents")

            self.assertEqual(episodes[0].id, episode_id)
            self.assertIn("Jira", episodes[0].summary)

    def test_retrieve_memory_context_formats_semantic_and_episodic_memory(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Project", "Niko has a local harness memory baseline")
            store.add_episode("Niko discussed harness memory with the user")

            with patch.dict(
                os.environ,
                {
                    "NIKO_STATE_DIR": temp_dir,
                    "NIKO_RUNTIME_CONFIG_FILE": str(Path(temp_dir) / "config.json"),
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_TOP_K": "3",
                },
                clear=False,
            ):
                memory = retrieve_memory_context("harness memory", message, store=store)

        self.assertTrue(memory.enabled)
        self.assertIn("Semantic memory", memory.text)
        self.assertIn("Episodic memory", memory.text)
        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertEqual(memory.to_meta()["episode_count"], 1)

    def test_fact_inventory_question_lists_recent_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Vẻ ngoài", "Tất cả các anh đều đẹp trai", source="ops")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_TOP_K": "3",
                },
                clear=False,
            ):
                memory = retrieve_memory_context("có fact nào em đang lưu không", message, store=store)

        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertEqual(memory.to_meta()["episode_count"], 0)
        self.assertIn("Vẻ ngoài", memory.text)
        self.assertIn("Tất cả các anh đều đẹp trai", memory.text)

    def test_truth_inventory_question_lists_recent_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Vẻ ngoài", "Tất cả các anh đều đẹp trai", source="ops")

            with patch.dict(os.environ, {"NIKO_MEMORY_ENABLED": "1", "NIKO_MEMORY_RETRIEVAL_ENABLED": "1"}, clear=False):
                memory = retrieve_memory_context("em có sự thật nào đang lưu không", message, store=store)

        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertIn("Vẻ ngoài", memory.text)

    def test_truth_inventory_question_with_filter_searches_matching_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Vũ", "Cu to", source="ops")
            store.add_fact("Vẻ ngoài", "Tất cả các anh đều đẹp trai", source="ops")

            with patch.dict(os.environ, {"NIKO_MEMORY_ENABLED": "1", "NIKO_MEMORY_RETRIEVAL_ENABLED": "1"}, clear=False):
                memory = retrieve_memory_context("có sự thật nào về vẻ ngoài em đang lưu?", message, store=store)

        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertIn("Vẻ ngoài", memory.text)
        self.assertNotIn("Vũ", memory.text)

    def test_vietnamese_fact_search_uses_normalized_terms(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        cuong = "C\u01b0\u1eddng"
        ve_ngoai = "V\u1ebb ngo\u00e0i"

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact(ve_ngoai, "Tat ca cac anh deu dep trai", source="ops")
            store.add_fact(cuong, "Cu to hon Vu", source="ops")

            facts = store.search_facts("em co fact ve Cuong khong", top_k=3)
            with patch.dict(os.environ, {"NIKO_MEMORY_ENABLED": "1", "NIKO_MEMORY_RETRIEVAL_ENABLED": "1"}, clear=False):
                memory = retrieve_memory_context(
                    f"em c\u00f3 fact v\u1ec1 {cuong} kh\u00f4ng?",
                    message,
                    store=store,
                )

        self.assertEqual([(fact.subject, fact.content) for fact in facts], [(cuong, "Cu to hon Vu")])
        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertIn(cuong, memory.text)
        self.assertNotIn(ve_ngoai, memory.text)

    def test_generic_weather_statement_does_not_match_fact_by_stopwords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Vẻ ngoài", "Tất cả các anh đều đẹp trai", source="ops")

            facts = store.search_facts("anh khẳng định với em nay trời nắng", top_k=3)

        self.assertEqual(facts, [])

    def test_deep_agent_prompt_includes_memory_context(self):
        message = telegram_message_to_gateway(
            {
                "text": "Can Niko use memory?",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_PROMPT_HOOK_FILE": "",
                    "CHAT_IDENTITY_ENABLED": "0",
                    "CLAUDE_DEEP_AGENT_COMMAND": "fcc-claude -p",
                },
                clear=True,
            ), patch("niko.memory.context.default_memory_store", return_value=store), patch(
                "niko.runtime.run_cli", return_value="OK"
            ) as run_cli:
                self.assertEqual(call_deep_agent("semantic facts SQLite", message, trace_id="turn-1", trace_logger=trace_logger), "OK")

            prompt = run_cli.call_args.args[1]
            events = trace_logger.read_events()

        self.assertIn("Semantic memory / facts", prompt)
        self.assertIn("Niko stores semantic facts in SQLite", prompt)
        self.assertTrue(any(event.get("kind") == "memory_retrieval" for event in events))


if __name__ == "__main__":
    unittest.main()
