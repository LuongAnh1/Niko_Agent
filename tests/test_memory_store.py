import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bots.decision_model.memory import (
    MEMORY_DISCARD,
    MEMORY_EPISODIC_EVENT,
    MEMORY_REMEMBER,
    MEMORY_RETRIEVE,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_SEMANTIC_FACT,
    MEMORY_SKIP,
    MEMORY_CORRECTION_NONE,
    MEMORY_CORRECT_MEMORY,
    MEMORY_FORGET_MEMORY,
    MEMORY_TARGET_FACT,
    MEMORY_TARGET_EPISODE,
    MemoryCorrectionDecision,
    MemoryCandidateDecision,
    MemoryRetrievalDecision,
    MemoryWriteDecision,
    apply_memory_correction_prompt_hint,
    build_memory_correction_criteria,
    build_memory_correction_instructions,
    build_memory_correction_state,
    normalize_memory_correction_choice,
    normalize_memory_target_type,
)
from niko.chat_gateway import telegram_message_to_gateway
from niko.harness.trace import TraceLogger
from niko.memory.consolidation import MemoryConsolidator
from niko.memory.context import RetrievedMemory, retrieve_memory_context
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore
from niko.runtime import call_deep_agent


class MemoryStoreTests(unittest.TestCase):
    def test_chat_log_consolidation_migration_adds_column_to_old_db(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "memory.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(
                    """
                    CREATE TABLE chat_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    INSERT INTO chat_log(session_id, role, content, source, meta_json, created_at)
                    VALUES ('chat-1', 'user', 'hello', 'test', '{}', '2026-10-07T00:00:00Z');
                    """
                )
                conn.commit()
            finally:
                conn.close()

            store = MemoryStore(db_path)

            with store.connection() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(chat_log)").fetchall()}
            rows = store.list_unconsolidated_chat()

        self.assertIn("consolidated", columns)
        self.assertEqual(rows[0]["id"], 1)
        self.assertFalse(rows[0]["consolidated"])

    def test_unconsolidated_chat_batch_orders_and_filters_by_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.log_chat("chat-1", "user", "tin dau", source="test")
            other = store.log_chat("chat-2", "user", "tin khac", source="test")
            second = store.log_chat("chat-1", "assistant", "tin hai", source="test")

            marked_count = store.mark_chat_consolidated([first])
            all_rows = store.list_unconsolidated_chat(limit=10)
            session_rows = store.list_unconsolidated_chat(limit=10, session_id="chat-1")

        self.assertEqual(marked_count, 1)
        self.assertEqual([row["id"] for row in all_rows], [other, second])
        self.assertEqual([row["id"] for row in session_rows], [second])
        self.assertTrue(all(not row["consolidated"] for row in all_rows))

    def test_consolidator_preview_and_mark_batch_does_not_touch_new_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.log_chat("chat-1", "user", "can nho cai nay", source="test")
            second = store.log_chat("chat-1", "assistant", "da ghi nhan", source="test")
            consolidator = MemoryConsolidator(store=store, batch_size=2)

            batch = consolidator.preview_next_batch()
            third = store.log_chat("chat-1", "user", "tin moi den sau batch", source="test")
            result = consolidator.mark_batch_done(batch.row_ids, reason="unit_test")
            remaining = store.list_unconsolidated_chat(limit=10)

        self.assertEqual(batch.row_ids, [first, second])
        self.assertEqual(result.status, "marked")
        self.assertEqual(result.marked_count, 2)
        self.assertEqual([row["id"] for row in remaining], [third])

    def test_memory_runtime_exposes_consolidation_scaffold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            row_id = store.log_chat("chat-1", "user", "memory scaffold", source="test")
            runtime = MemoryRuntime(store=store)

            batch = runtime.preview_consolidation_batch(limit=5)
            result = runtime.mark_consolidation_batch(batch.row_ids, reason="unit_test")
            remaining = store.list_unconsolidated_chat(limit=5)

        self.assertEqual(batch.row_ids, [row_id])
        self.assertEqual(result.marked_count, 1)
        self.assertEqual(remaining, [])

    def test_consolidation_run_writes_explicit_semantic_fact_and_marks_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            row_id = store.log_chat("chat-1", "user", "Ghi nhớ rằng anh thích dashboard rõ ràng", source="test")
            consolidator = MemoryConsolidator(
                store=store,
                candidate_classifier=lambda _candidate: MemoryCandidateDecision(
                    decision=MEMORY_SEMANTIC_FACT,
                    reason="explicit fact",
                ),
            )

            result = consolidator.run_once(limit=5)
            snapshot = store.snapshot()
            remaining = store.list_unconsolidated_chat(limit=5)

        self.assertEqual(result.status, "stored")
        self.assertEqual(result.marked_count, 1)
        self.assertEqual(result.facts_written, [snapshot["facts"][0]["id"]])
        self.assertIn("dashboard", snapshot["facts"][0]["content"])
        self.assertEqual(snapshot["facts"][0]["source"], "consolidation")
        self.assertEqual(remaining, [])
        self.assertEqual(result.batch.row_ids, [row_id])

    def test_consolidation_explicit_fact_strips_prefix_and_skips_duplicate_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat(
                "chat-1",
                "user",
                "Ghi nhớ rằng anh thích checklist có mục đích rõ ràng và chia theo từng phase.",
                source="test",
            )
            store.log_chat("chat-1", "assistant", "Đã ghi nhớ theo phase rõ ràng.", source="test")
            consolidator = MemoryConsolidator(store=store)

            batch = consolidator.preview_next_batch(limit=5)
            candidates = consolidator.build_candidates(batch)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].kind_hint, MEMORY_SEMANTIC_FACT)
        self.assertEqual(candidates[0].reason, "explicit_memory_statement")
        self.assertEqual(
            candidates[0].content,
            "anh thích checklist có mục đích rõ ràng và chia theo từng phase.",
        )

    def test_consolidation_run_writes_episodic_event_and_marks_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat("chat-1", "user", "Implement phần memory consolidation giúp anh", source="test")
            store.log_chat("chat-1", "assistant", "Đã triển khai và test phần consolidation", source="test")
            consolidator = MemoryConsolidator(
                store=store,
                candidate_classifier=lambda _candidate: MemoryCandidateDecision(
                    decision=MEMORY_EPISODIC_EVENT,
                    reason="completed task",
                ),
            )

            result = consolidator.run_once(limit=5)
            snapshot = store.snapshot()

        self.assertEqual(result.status, "stored")
        self.assertEqual(result.marked_count, 2)
        self.assertEqual(snapshot["counts"]["episodes"], 1)
        self.assertEqual(snapshot["episodes"][0]["source"], "consolidation")
        self.assertIn("Implement", snapshot["episodes"][0]["summary"])

    def test_consolidation_discard_marks_small_talk_done_without_long_term_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat("chat-1", "user", "chào em", source="test")
            consolidator = MemoryConsolidator(
                store=store,
                candidate_classifier=lambda _candidate: MemoryCandidateDecision(
                    decision=MEMORY_DISCARD,
                    reason="small talk",
                ),
            )

            result = consolidator.run_once(limit=5)
            snapshot = store.snapshot()
            remaining = store.list_unconsolidated_chat(limit=5)

        self.assertEqual(result.status, "discarded")
        self.assertEqual(result.marked_count, 1)
        self.assertEqual(snapshot["counts"]["facts"], 0)
        self.assertEqual(snapshot["counts"]["episodes"], 0)
        self.assertEqual(remaining, [])

    def test_consolidation_classifier_error_does_not_mark_rows(self):
        def failing_classifier(_candidate):
            raise RuntimeError("ollama offline")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            row_id = store.log_chat("chat-1", "user", "Ghi nhớ rằng anh dùng Niko local", source="test")
            consolidator = MemoryConsolidator(store=store, candidate_classifier=failing_classifier)

            result = consolidator.run_once(limit=5)
            remaining = store.list_unconsolidated_chat(limit=5)

        self.assertEqual(result.status, "error")
        self.assertIn("ollama offline", result.error)
        self.assertEqual([row["id"] for row in remaining], [row_id])

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

    def test_empty_or_wordless_search_returns_no_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")
            store.add_episode("Niko discussed harness memory with the user")

            self.assertEqual(store.search_facts(""), [])
            self.assertEqual(store.search_facts("   "), [])
            self.assertEqual(store.search_facts("?!_"), [])
            self.assertEqual(store.search_episodes(""), [])
            self.assertEqual(store.search_episodes("   "), [])
            self.assertEqual(store.search_episodes("?!_"), [])

    def test_search_splits_underscore_terms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Runtime", "Niko agent stores dashboard config", source="test")
            episode_id = store.add_episode("Niko agent warmed up the decision model", source="test")

            facts = store.search_facts("niko_agent")
            episodes = store.search_episodes("niko_agent")

            self.assertEqual(facts[0].id, fact_id)
            self.assertEqual(episodes[0].id, episode_id)

    def test_search_does_not_match_token_prefixes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Carpet", "Blue carpet sample", source="test")
            store.add_episode("The team cleaned a blue carpet", source="test")

            self.assertEqual(store.search_facts("car"), [])
            self.assertEqual(store.search_episodes("car"), [])

    def test_search_fallback_without_fts_uses_normalized_tokens(self):
        cuong = "C\u01b0\u1eddng"

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact(cuong, "Niko agent owner", source="test")
            episode_id = store.add_episode(f"{cuong} adjusted Niko agent memory", source="test")
            with store.connection() as conn:
                conn.execute("DROP TABLE IF EXISTS facts_fts")
                conn.execute("DROP TABLE IF EXISTS episodes_fts")

            facts = store.search_facts("Cuong")
            episodes = store.search_episodes("Cuong")
            prefix_facts = store.search_facts("own")
            prefix_episodes = store.search_episodes("adj")

        self.assertEqual(facts[0].id, fact_id)
        self.assertEqual(episodes[0].id, episode_id)
        self.assertEqual(prefix_facts, [])
        self.assertEqual(prefix_episodes, [])

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
        self.assertIn("Relevant semantic facts", memory.text)
        self.assertIn("Relevant episodic events", memory.text)
        self.assertEqual(memory.to_meta()["fact_count"], 1)
        self.assertEqual(memory.to_meta()["episode_count"], 1)
        self.assertEqual(memory.to_meta()["recent_turn_count"], 0)

    def test_retrieve_memory_context_includes_recent_conversation_window(self):
        message = telegram_message_to_gateway(
            {
                "text": "current prompt",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat("456", "user", "old question", source="telegram", meta={"route": "deep_agent"})
            store.log_chat("456", "assistant", "old answer", source="telegram", meta={"route": "deep_agent"})
            store.log_chat("456", "user", "current prompt", source="telegram", meta={"route": "incoming"})

            with patch.dict(
                os.environ,
                {
                    "NIKO_STATE_DIR": temp_dir,
                    "NIKO_RUNTIME_CONFIG_FILE": str(Path(temp_dir) / "config.json"),
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_RECENT_TURNS": "4",
                },
                clear=False,
            ):
                memory = retrieve_memory_context("current prompt", message, store=store)

        self.assertIn("Recent conversation:", memory.text)
        self.assertIn("old question", memory.text)
        self.assertIn("old answer", memory.text)
        self.assertNotIn("current prompt", [turn["content"] for turn in memory.recent_turns])
        self.assertEqual(memory.to_meta()["recent_turn_count"], 2)

    def test_memory_runtime_retrieve_for_deep_matches_context_wrapper(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")
            runtime = MemoryRuntime(store=store)

            with patch.dict(
                os.environ,
                {"NIKO_MEMORY_ENABLED": "1", "NIKO_MEMORY_RETRIEVAL_ENABLED": "1"},
                clear=False,
            ):
                runtime_memory = runtime.retrieve_for_deep("semantic facts SQLite", message)
                wrapper_memory = retrieve_memory_context("semantic facts SQLite", message, store=store)

        self.assertEqual(runtime_memory.text, wrapper_memory.text)
        self.assertEqual(runtime_memory.to_meta(), wrapper_memory.to_meta())

    def test_retrieve_memory_context_delegates_to_memory_runtime(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            expected = RetrievedMemory(text="memory text", facts=[], episodes=[], enabled=True)

            with patch("niko.memory.runtime.MemoryRuntime.retrieve_for_deep", return_value=expected) as retrieve:
                memory = retrieve_memory_context("anything", message, store=store)

        self.assertIs(memory, expected)
        retrieve.assert_called_once_with("anything", gateway_message=message)

    def test_memory_runtime_accepts_fake_retrieval_decider(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    query="semantic facts SQLite",
                    reason="test gate",
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                memory = runtime.retrieve_for_deep("em co nho khong", message)

        self.assertEqual(memory.to_meta()["gate_decision"], MEMORY_RETRIEVE)
        self.assertEqual(memory.to_meta()["gate_query"], "semantic facts SQLite")
        self.assertIn("Niko stores semantic facts in SQLite", memory.text)

    def test_memory_runtime_write_gate_off_records_deep_episode(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            decider = Mock(return_value=MemoryWriteDecision(decision=MEMORY_DISCARD))
            runtime = MemoryRuntime(store=store, write_decider=decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_GATE_ENABLED": "0",
                },
                clear=False,
            ):
                runtime.record_deep_episode(
                    "456",
                    "phan tich giup anh",
                    "day la cau tra loi",
                    message,
                    [],
                    "turn-1",
                    trace_logger,
                )

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        decider.assert_not_called()
        self.assertEqual(snapshot["counts"]["episodes"], 1)
        self.assertIn("phan tich giup anh", snapshot["episodes"][0]["summary"])
        self.assertFalse(any(event.get("kind") == "memory_write_decision" for event in events))

    def test_memory_runtime_write_gate_remember_records_episode_and_trace(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime = MemoryRuntime(
                store=store,
                write_decider=lambda _prompt, _answer, _followups, _message, _route: MemoryWriteDecision(
                    decision=MEMORY_REMEMBER,
                    reason="substantive task",
                    confidence=0.9,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                runtime.record_deep_episode(
                    "456",
                    "phan tich giup anh",
                    "day la cau tra loi",
                    message,
                    ["them context"],
                    "turn-1",
                    trace_logger,
                )

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        self.assertEqual(snapshot["counts"]["episodes"], 1)
        decision_events = [event for event in events if event.get("kind") == "memory_write_decision"]
        self.assertEqual(decision_events[0]["data"]["decision"], MEMORY_REMEMBER)
        self.assertEqual(decision_events[0]["data"]["confidence"], 0.9)
        self.assertTrue(any(event.get("kind") == "memory_write_episode" for event in events))

    def test_memory_runtime_write_gate_discard_skips_episode_but_keeps_chat_log(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime = MemoryRuntime(
                store=store,
                write_decider=lambda _prompt, _answer, _followups, _message, _route: MemoryWriteDecision(
                    decision=MEMORY_DISCARD,
                    reason="small talk",
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                runtime.record_chat_log("456", "assistant", "chao anh", message, "local_reply", "turn-1", trace_logger)
                runtime.record_deep_episode("456", "chao", "chao anh", message, [], "turn-1", trace_logger)

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        self.assertEqual(snapshot["counts"]["chat_log"], 1)
        self.assertEqual(snapshot["counts"]["episodes"], 0)
        decision_events = [event for event in events if event.get("kind") == "memory_write_decision"]
        self.assertEqual(decision_events[0]["data"]["decision"], MEMORY_DISCARD)

    def test_memory_runtime_write_gate_discard_skips_memory_inventory_episode(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            decider = Mock(
                return_value=MemoryWriteDecision(
                    decision=MEMORY_DISCARD,
                    reason="memory inventory only",
                )
            )
            runtime = MemoryRuntime(store=store, write_decider=decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                runtime.record_deep_episode(
                    "456",
                    "Hien tai em dang luu nhung fact nao ve anh?",
                    "Em dang luu 2 fact ve anh trong semantic memory.",
                    message,
                    [],
                    "turn-1",
                    trace_logger,
                )

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        decider.assert_called_once()
        self.assertIn("dang luu nhung fact", decider.call_args.args[0])
        self.assertEqual(snapshot["counts"]["episodes"], 0)
        decision_events = [event for event in events if event.get("kind") == "memory_write_decision"]
        self.assertEqual(decision_events[0]["data"]["decision"], MEMORY_DISCARD)
        self.assertEqual(decision_events[0]["data"]["reason"], "memory inventory only")
        self.assertFalse(any(event.get("kind") == "memory_write_episode" for event in events))

    def test_memory_runtime_write_gate_error_fail_open_records_episode(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )

        def failing_decider(_prompt, _answer, _followups, _message, _route):
            raise RuntimeError("ollama offline")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            trace_logger = TraceLogger(Path(temp_dir) / "traces", enabled=True)
            runtime = MemoryRuntime(store=store, write_decider=failing_decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                runtime.record_deep_episode(
                    "456",
                    "phan tich giup anh",
                    "day la cau tra loi",
                    message,
                    [],
                    "turn-1",
                    trace_logger,
                )

            snapshot = store.snapshot()
            events = trace_logger.read_events()

        self.assertEqual(snapshot["counts"]["episodes"], 1)
        error_events = [event for event in events if event.get("kind") == "memory_write_gate_error"]
        self.assertIn("ollama offline", error_events[0]["data"]["error"])
        decision_events = [event for event in events if event.get("kind") == "memory_write_decision"]
        self.assertEqual(decision_events[0]["data"]["decision"], MEMORY_REMEMBER)

    def test_decision_model_fact_inventory_lists_recent_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite", source="ops")
            store.add_episode("Niko discussed an unrelated episode", source="ops")
            decider = Mock(
                return_value=MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    reason="fact inventory",
                    fact_mode=MEMORY_RETRIEVAL_LIST,
                    episode_mode=MEMORY_RETRIEVAL_NONE,
                )
            )
            runtime = MemoryRuntime(store=store, retrieval_decider=decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_MEMORY_TOP_K": "3",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                memory = runtime.retrieve_for_deep("Hien tai em dang luu nhung fact nao ve anh?", message)

        meta = memory.to_meta()
        decider.assert_called_once()
        self.assertEqual(meta["gate_decision"], MEMORY_RETRIEVE)
        self.assertEqual(meta["gate_fact_mode"], MEMORY_RETRIEVAL_LIST)
        self.assertEqual(meta["gate_episode_mode"], MEMORY_RETRIEVAL_NONE)
        self.assertEqual(meta["fact_count"], 1)
        self.assertEqual(meta["episode_count"], 0)
        self.assertIn("Niko stores semantic facts in SQLite", memory.text)

    def test_decision_model_fact_inventory_with_topic_searches_matching_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Dashboard", "Niko dashboard owns runtime config", source="ops")
            store.add_fact("Jira", "Jira lane is a future business gateway", source="ops")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    query="dashboard runtime config",
                    reason="topic inventory",
                    fact_mode=MEMORY_RETRIEVAL_SEARCH,
                    episode_mode=MEMORY_RETRIEVAL_NONE,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ), patch.object(store, "list_facts", wraps=store.list_facts) as list_facts:
                memory = runtime.retrieve_for_deep("Em dang luu fact nao ve dashboard?", message)

        self.assertEqual(memory.to_meta()["gate_fact_mode"], MEMORY_RETRIEVAL_SEARCH)
        self.assertIn("Niko dashboard owns runtime config", memory.text)
        self.assertNotIn("Jira lane", memory.text)
        list_facts.assert_not_called()

    def test_decision_model_episode_inventory_uses_recent_events(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite", source="ops")
            store.add_episode("Niko helped verify the memory write gate", source="ops")
            runtime = MemoryRuntime(
                store=store,
                retrieval_decider=lambda _prompt, _message: MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    reason="episode inventory",
                    fact_mode=MEMORY_RETRIEVAL_NONE,
                    episode_mode=MEMORY_RETRIEVAL_RECENT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ):
                memory = runtime.retrieve_for_deep("Gan day em nho nhung episode nao?", message)

        meta = memory.to_meta()
        self.assertEqual(meta["gate_fact_mode"], MEMORY_RETRIEVAL_NONE)
        self.assertEqual(meta["gate_episode_mode"], MEMORY_RETRIEVAL_RECENT)
        self.assertEqual(meta["fact_count"], 0)
        self.assertEqual(meta["episode_count"], 1)
        self.assertIn("Niko helped verify the memory write gate", memory.text)

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

    def test_vietnamese_episode_search_uses_normalized_terms(self):
        cuong = "C\u01b0\u1eddng"

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            episode_id = store.add_episode(f"Niko \u0111\u00e3 trao \u0111\u1ed5i v\u1edbi {cuong} v\u1ec1 memory", source="test")

            episodes = store.search_episodes("Cuong", top_k=3)

        self.assertEqual(episodes[0].id, episode_id)
        self.assertIn(cuong, episodes[0].summary)

    def test_generic_weather_statement_does_not_match_fact_by_stopwords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Vẻ ngoài", "Tất cả các anh đều đẹp trai", source="ops")

            facts = store.search_facts("anh khẳng định với em nay trời nắng", top_k=3)

        self.assertEqual(facts, [])

    def test_memory_gate_skip_does_not_search_store(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ), patch(
                "niko.memory.runtime.decide_memory_retrieval",
                return_value=MemoryRetrievalDecision(decision=MEMORY_SKIP, reason="standalone"),
            ), patch.object(store, "search_facts", wraps=store.search_facts) as search_facts, patch.object(
                store, "search_episodes", wraps=store.search_episodes
            ) as search_episodes:
                memory = retrieve_memory_context("thời tiết hôm nay", message, store=store)

        self.assertTrue(memory.enabled)
        self.assertEqual(memory.text, "")
        self.assertEqual(memory.to_meta()["gate_decision"], MEMORY_SKIP)
        search_facts.assert_not_called()
        search_episodes.assert_not_called()

    def test_memory_gate_retrieve_uses_gate_query(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ), patch(
                "niko.memory.runtime.decide_memory_retrieval",
                return_value=MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    query="semantic facts SQLite",
                    reason="asks about memory",
                    confidence=0.8,
                ),
            ), patch.object(store, "search_facts", wraps=store.search_facts) as search_facts:
                memory = retrieve_memory_context("em có nhớ không", message, store=store)

        self.assertEqual(memory.to_meta()["gate_decision"], MEMORY_RETRIEVE)
        self.assertEqual(memory.to_meta()["gate_query"], "semantic facts SQLite")
        self.assertEqual(memory.to_meta()["gate_confidence"], 0.8)
        self.assertIn("Niko stores semantic facts in SQLite", memory.text)
        search_facts.assert_called_once()
        self.assertEqual(search_facts.call_args.args[0], "semantic facts SQLite")

    def test_memory_gate_error_fail_open_retrieves_raw_prompt(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ), patch(
                "niko.memory.runtime.decide_memory_retrieval",
                side_effect=RuntimeError("ollama offline"),
            ):
                memory = retrieve_memory_context("semantic facts SQLite", message, store=store)

        meta = memory.to_meta()
        self.assertEqual(meta["gate_decision"], MEMORY_RETRIEVE)
        self.assertIn("ollama offline", meta["gate_error"])
        self.assertIn("Niko stores semantic facts in SQLite", memory.text)

    def test_fact_inventory_uses_memory_gate_mode_to_list_facts(self):
        message = telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Luong"},
                "chat": {"id": 456, "type": "private"},
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Harness", "Niko stores semantic facts in SQLite")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_RETRIEVAL_ENABLED": "1",
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                },
                clear=False,
            ), patch(
                "niko.memory.runtime.decide_memory_retrieval",
                return_value=MemoryRetrievalDecision(
                    decision=MEMORY_RETRIEVE,
                    reason="fact inventory",
                    fact_mode=MEMORY_RETRIEVAL_LIST,
                    episode_mode=MEMORY_RETRIEVAL_NONE,
                    label="retrieve",
                ),
            ) as gate:
                memory = retrieve_memory_context("em đang lưu fact nào", message, store=store)

        gate.assert_called_once()
        self.assertEqual(memory.to_meta()["gate_label"], "retrieve")
        self.assertEqual(memory.to_meta()["gate_fact_mode"], MEMORY_RETRIEVAL_LIST)
        self.assertEqual(memory.to_meta()["gate_episode_mode"], MEMORY_RETRIEVAL_NONE)
        self.assertIn("Niko stores semantic facts in SQLite", memory.text)

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
            ), patch("niko.memory.runtime.default_memory_store", return_value=store), patch(
                "niko.runtime.run_cli", return_value="OK"
            ) as run_cli:
                self.assertEqual(call_deep_agent("semantic facts SQLite", message, trace_id="turn-1", trace_logger=trace_logger), "OK")

            prompt = run_cli.call_args.args[1]
            events = trace_logger.read_events()

        self.assertIn("Relevant semantic facts", prompt)
        self.assertIn("Niko stores semantic facts in SQLite", prompt)
        self.assertIn("Current user message", prompt)
        self.assertTrue(any(event.get("kind") == "memory_retrieval" for event in events))

    def test_deep_agent_trace_includes_memory_gate_metadata(self):
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
                    "NIKO_MEMORY_GATE_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "0",
                    "NIKO_PROMPT_HOOK_FILE": "",
                    "CHAT_IDENTITY_ENABLED": "0",
                    "CLAUDE_DEEP_AGENT_COMMAND": "fcc-claude -p",
                },
                clear=True,
            ), patch("niko.memory.runtime.default_memory_store", return_value=store), patch(
                "niko.memory.runtime.decide_memory_retrieval",
                return_value=MemoryRetrievalDecision(decision=MEMORY_SKIP, reason="standalone"),
            ), patch(
                "niko.runtime.run_cli", return_value="OK"
            ) as run_cli:
                self.assertEqual(call_deep_agent("generic standalone", message, trace_id="turn-1", trace_logger=trace_logger), "OK")

            prompt = run_cli.call_args.args[1]
            events = trace_logger.read_events()

        self.assertNotIn("Relevant semantic facts", prompt)
        memory_events = [event for event in events if event.get("kind") == "memory_retrieval"]
        self.assertEqual(memory_events[0]["data"]["gate_decision"], MEMORY_SKIP)
        self.assertEqual(memory_events[0]["data"]["recent_turn_count"], 0)


class MemoryCorrectionRuntimeTests(unittest.TestCase):
    def _message(self):
        return telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Tran Anh"},
                "chat": {"id": 456, "type": "private"},
            }
        )

    def _trace(self, temp_dir: str) -> TraceLogger:
        return TraceLogger(Path(temp_dir) / "traces")

    def test_memory_correction_decision_helpers_define_closed_intent_task(self):
        state = build_memory_correction_state(
            "sửa fact checklist thành checklist theo phase",
            self._message(),
            decision_context={
                "recent_turns": [
                    {
                        "role": "assistant",
                        "content": "Anh chọn một trong các fact này giúp em: fact #8, fact #6.",
                        "route": "memory_correction",
                    }
                ],
                "active_workflow": "memory_correction",
                "pending_action": MEMORY_FORGET_MEMORY,
                "pending_choices": [{"type": MEMORY_TARGET_FACT, "id": 8}],
                "pending_replacement": "",
            },
        )
        criteria = build_memory_correction_criteria()
        instructions = build_memory_correction_instructions()

        self.assertEqual(state["current_prompt"], "sửa fact checklist thành checklist theo phase")
        self.assertEqual(state["prompt"], "sửa fact checklist thành checklist theo phase")
        self.assertEqual(state["active_workflow"], "memory_correction")
        self.assertEqual(state["pending_action"], MEMORY_FORGET_MEMORY)
        self.assertEqual(state["pending_choices"], [{"type": MEMORY_TARGET_FACT, "id": 8}])
        self.assertEqual(state["recent_turns"][0]["route"], "memory_correction")
        self.assertIn("primary conversation context", state["decision_context"])
        self.assertIn("auxiliary metadata", state["decision_context"])
        self.assertIn(MEMORY_CORRECT_MEMORY, criteria)
        self.assertIn(MEMORY_FORGET_MEMORY, criteria)
        self.assertIn("replacement", instructions)
        self.assertIn("primary context", instructions)
        self.assertIn("auxiliary state", instructions)
        self.assertEqual(normalize_memory_correction_choice("delete-memory"), MEMORY_FORGET_MEMORY)
        self.assertEqual(normalize_memory_correction_choice("fix memory"), MEMORY_CORRECT_MEMORY)
        self.assertEqual(normalize_memory_correction_choice("skip"), "none")
        self.assertEqual(normalize_memory_target_type("semantic_fact"), MEMORY_TARGET_FACT)
        self.assertEqual(normalize_memory_target_type("episodic"), MEMORY_TARGET_EPISODE)
        self.assertEqual(
            apply_memory_correction_prompt_hint("Niko, quên fact checklist màu xanh", MEMORY_CORRECT_MEMORY),
            MEMORY_FORGET_MEMORY,
        )

    def test_memory_correction_disabled_does_not_call_decider_or_mutate(self):
        def failing_decider(_prompt, _gateway):
            raise AssertionError("decider should not run while disabled")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            runtime = MemoryRuntime(store=store, correction_decider=failing_decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "0",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

            facts = store.list_facts()

        self.assertFalse(result.handled)
        self.assertEqual([fact.id for fact in facts], [fact_id])

    def test_memory_correction_forgets_unique_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist rõ ràng",
                    target_type=MEMORY_TARGET_FACT,
                    reason="explicit forget",
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact về checklist rõ ràng",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

            facts = store.list_facts()

        self.assertTrue(result.handled)
        self.assertIn(f"fact #{fact_id}", result.reply)
        self.assertEqual(facts, [])

    def test_memory_correction_asks_when_forget_match_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            second = store.add_fact("Project memory", "checklist phase cần mục đích", source="test")
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

            facts = store.list_facts()

        self.assertTrue(result.handled)
        self.assertIn("nhiều fact", result.reply)
        self.assertEqual({fact.id for fact in facts}, {first, second})

    def test_memory_correction_pending_fact_tag_applies_previous_forget_intent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Test correction", "anh thích checklist màu xanh", source="test")
            second = store.add_fact("Chat memory", "anh thích checklist theo phase", source="test")
            seen_contexts: list[dict] = []

            def context_aware_decider(prompt, _gateway, decision_context=None):
                seen_contexts.append(decision_context or {})
                if "fact #" in prompt:
                    recent_turns = decision_context.get("recent_turns", []) if decision_context else []
                    has_clarifying_context = any(
                        turn.get("role") == "assistant" and "fact #" in turn.get("content", "")
                        for turn in recent_turns
                    )
                    if not has_clarifying_context:
                        return MemoryCorrectionDecision(decision=MEMORY_CORRECTION_NONE)
                    return MemoryCorrectionDecision(
                        decision=MEMORY_FORGET_MEMORY,
                        target_type=MEMORY_TARGET_FACT,
                        reason="followup_choice",
                    )
                return MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist",
                    target_type=MEMORY_TARGET_FACT,
                    reason="explicit forget",
                )

            runtime = MemoryRuntime(
                store=store,
                correction_decider=context_aware_decider,
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                clarify = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )
                store.log_chat(
                    "456",
                    "user",
                    "quên fact checklist",
                    source="test",
                    meta={"route": "incoming"},
                )
                store.log_chat(
                    "456",
                    "assistant",
                    clarify.reply,
                    source="test",
                    meta={"route": "memory_correction"},
                )
                applied = runtime.handle_memory_correction(
                    "456",
                    f"fact #{first} nhé",
                    self._message(),
                    "trace-2",
                    self._trace(temp_dir),
                )

            facts = store.list_facts()

        self.assertTrue(clarify.handled)
        self.assertIn("nhiều fact", clarify.reply)
        self.assertTrue(applied.handled)
        self.assertIn(f"fact #{first}", applied.reply)
        self.assertEqual([fact.id for fact in facts], [second])
        self.assertEqual(seen_contexts[1]["active_workflow"], "memory_correction")
        self.assertEqual(seen_contexts[1]["pending_action"], MEMORY_FORGET_MEMORY)
        self.assertEqual(seen_contexts[1]["recent_turns"][-1]["role"], "assistant")
        self.assertEqual({choice["id"] for choice in seen_contexts[1]["pending_choices"]}, {first, second})

    def test_memory_correction_pending_choice_uses_pending_action_when_model_mislabels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Test correction", "anh thích checklist màu xanh", source="test")
            second = store.add_fact("Chat memory", "anh thích checklist theo phase", source="test")
            calls = 0

            def mislabeling_decider(_prompt, _gateway, decision_context=None):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return MemoryCorrectionDecision(
                        decision=MEMORY_FORGET_MEMORY,
                        query="checklist",
                        target_type=MEMORY_TARGET_FACT,
                        reason="explicit forget",
                    )
                self.assertEqual(decision_context["pending_action"], MEMORY_FORGET_MEMORY)
                return MemoryCorrectionDecision(
                    decision=MEMORY_CORRECT_MEMORY,
                    target_type=MEMORY_TARGET_FACT,
                    reason="model misread selection as correction",
                )

            runtime = MemoryRuntime(store=store, correction_decider=mislabeling_decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                clarify = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )
                store.log_chat(
                    "456",
                    "assistant",
                    clarify.reply,
                    source="test",
                    meta={"route": "memory_correction"},
                )
                applied = runtime.handle_memory_correction(
                    "456",
                    f"fact #{first} nhé",
                    self._message(),
                    "trace-2",
                    self._trace(temp_dir),
                )

            facts = store.list_facts()

        self.assertTrue(applied.handled)
        self.assertIn(f"fact #{first}", applied.reply)
        self.assertEqual([fact.id for fact in facts], [second])
        self.assertEqual(applied.decision["decision"], MEMORY_FORGET_MEMORY)
        self.assertEqual(applied.decision["model_decision"], MEMORY_CORRECT_MEMORY)

    def test_memory_correction_decision_context_excludes_current_incoming_prompt(self):
        captured_contexts: list[dict] = []

        def capturing_decider(_prompt, _gateway, decision_context=None):
            captured_contexts.append(decision_context or {})
            return MemoryCorrectionDecision(decision="none")

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.log_chat("456", "user", "quên fact checklist màu xanh", source="test", meta={"route": "incoming"})
            store.log_chat(
                "456",
                "assistant",
                "Anh chọn một trong các fact này giúp em: fact #8, fact #6.",
                source="test",
                meta={"route": "memory_correction"},
            )
            store.log_chat("456", "user", "fact #8 nhé", source="test", meta={"route": "incoming"})
            runtime = MemoryRuntime(store=store, correction_decider=capturing_decider)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "fact #8 nhé",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

        self.assertFalse(result.handled)
        recent_turns = captured_contexts[0]["recent_turns"]
        self.assertEqual(recent_turns[-1]["role"], "assistant")
        self.assertNotIn("fact #8 nhé", [turn["content"] for turn in recent_turns])

    def test_memory_correction_updates_unique_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist ngắn", source="test")
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_CORRECT_MEMORY,
                    query="checklist ngắn",
                    target_type=MEMORY_TARGET_FACT,
                    replacement="anh thích checklist có mục đích rõ ràng theo từng phase",
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "sửa fact checklist ngắn thành checklist có mục đích rõ ràng",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

            fact = store.list_facts()[0]

        self.assertTrue(result.handled)
        self.assertEqual(fact.id, fact_id)
        self.assertIn("mục đích rõ ràng", fact.content)
        self.assertEqual(fact.meta["previous_content"], "anh thích checklist ngắn")

    def test_memory_correction_keeps_episode_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            episode_id = store.add_episode("Niko discussed a memory checklist", source="test")
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="memory checklist",
                    target_type=MEMORY_TARGET_EPISODE,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "xóa episode memory checklist",
                    self._message(),
                    "trace-1",
                    self._trace(temp_dir),
                )

            episodes = store.list_episodes()

        self.assertTrue(result.handled)
        self.assertIn("chưa cho sửa hoặc xóa qua chat", result.reply)
        self.assertEqual([episode.id for episode in episodes], [episode_id])


if __name__ == "__main__":
    unittest.main()
