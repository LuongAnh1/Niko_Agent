"""Regression tests cho memory correction loop default-off."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bots.decision_model.memory import (
    MEMORY_CORRECT_MEMORY,
    MEMORY_FORGET_MEMORY,
    MEMORY_TARGET_FACT,
    MemoryCorrectionDecision,
)
from niko.chat_gateway import telegram_message_to_gateway
from niko.harness.trace import TraceLogger
from niko.memory.correction_loop import MemoryCorrectionLoopWorkflow
from niko.memory.runtime import MemoryRuntime
from niko.memory.store import MemoryStore


class MemoryCorrectionLoopWorkflowTests(unittest.TestCase):
    def _message(self):
        return telegram_message_to_gateway(
            {
                "text": "hello",
                "from": {"id": 123, "first_name": "Tran Anh"},
                "chat": {"id": 456, "type": "private"},
            }
        )

    def _trace(self, temp_dir: str) -> TraceLogger:
        return TraceLogger(Path(temp_dir) / "traces", enabled=True)

    def _gate(self, decision: str, query: str, replacement: str = "") -> dict[str, object]:
        return {
            "enabled": True,
            "decision": decision,
            "query": query,
            "target_type": MEMORY_TARGET_FACT,
            "replacement": replacement,
            "reason": "test",
        }

    def test_loop_workflow_forgets_unique_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            workflow = MemoryCorrectionLoopWorkflow(store_provider=lambda: store)

            outcome = workflow.handle(
                prompt="quên fact checklist",
                gate=self._gate(MEMORY_FORGET_MEMORY, "checklist rõ ràng"),
                conversation_id="456",
                trace_id="trace-1",
                trace_logger=self._trace(temp_dir),
            )
            facts = store.list_facts()

        self.assertTrue(outcome.handled)
        self.assertEqual(outcome.action, "delete_fact")
        self.assertEqual(outcome.fact_id, fact_id)
        self.assertEqual(facts, [])

    def test_loop_workflow_clarifies_ambiguous_forget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            second = store.add_fact("Project memory", "checklist phase cần mục đích", source="test")
            workflow = MemoryCorrectionLoopWorkflow(store_provider=lambda: store)

            outcome = workflow.handle(
                prompt="quên fact checklist",
                gate=self._gate(MEMORY_FORGET_MEMORY, "checklist"),
                conversation_id="456",
                trace_id="trace-1",
                trace_logger=self._trace(temp_dir),
            )
            facts = store.list_facts()

        self.assertTrue(outcome.handled)
        self.assertEqual(outcome.reason, "ambiguous_fact_match")
        self.assertIn("nhiều fact", outcome.reply)
        self.assertEqual(set(outcome.fact_ids), {first, second})
        self.assertEqual({fact.id for fact in facts}, {first, second})

    def test_loop_workflow_updates_unique_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist ngắn", source="test")
            workflow = MemoryCorrectionLoopWorkflow(store_provider=lambda: store)

            outcome = workflow.handle(
                prompt="sửa fact checklist",
                gate=self._gate(
                    MEMORY_CORRECT_MEMORY,
                    "checklist ngắn",
                    replacement="anh thích checklist có mục đích rõ ràng",
                ),
                conversation_id="456",
                trace_id="trace-1",
                trace_logger=self._trace(temp_dir),
            )
            fact = store.list_facts()[0]

        self.assertTrue(outcome.handled)
        self.assertEqual(outcome.action, "update_fact")
        self.assertEqual(outcome.fact_id, fact_id)
        self.assertIn("mục đích rõ ràng", fact.content)

    def test_loop_workflow_clarifies_missing_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist ngắn", source="test")
            workflow = MemoryCorrectionLoopWorkflow(store_provider=lambda: store)

            outcome = workflow.handle(
                prompt="sửa fact checklist",
                gate=self._gate(MEMORY_CORRECT_MEMORY, "checklist ngắn"),
                conversation_id="456",
                trace_id="trace-1",
                trace_logger=self._trace(temp_dir),
            )
            fact = store.list_facts()[0]

        self.assertTrue(outcome.handled)
        self.assertEqual(outcome.reason, "missing_replacement")
        self.assertEqual(outcome.fact_ids, [fact_id])
        self.assertEqual(fact.content, "anh thích checklist ngắn")

    def test_runtime_uses_loop_path_when_enabled_and_writes_loop_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist rõ ràng",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist rõ ràng",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            events = trace_logger.read_events()

        self.assertTrue(result.handled)
        self.assertTrue(result.decision["loop_enabled"])
        self.assertIn(f"fact #{fact_id}", result.reply)
        self.assertIn("loop_started", {event.get("kind") for event in events})
        self.assertIn("memory_correction_applied", {event.get("kind") for event in events})

    def test_runtime_loop_logs_next_to_custom_trace_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist rõ ràng",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                    "NIKO_RUNTIME_LOG_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist rõ ràng",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            runtime_log_files = list((Path(temp_dir) / "logs").glob("*.jsonl"))
            runtime_log_text = "\n".join(path.read_text(encoding="utf-8") for path in runtime_log_files)

        self.assertTrue(result.handled)
        self.assertIn('"source": "loop"', runtime_log_text)
        self.assertIn('"event": "loop_started"', runtime_log_text)

    def test_runtime_loop_refines_empty_query_and_deletes_specific_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            target = store.add_fact("Loop test", "anh thích checklist màu xanh", source="test")
            other_a = store.add_fact("Anh", "Anh thích checklist có mục đích rõ ràng cho từng nhóm công việc.", source="test")
            other_b = store.add_fact("Loop test", "checklist phase cần mục đích rõ ràng", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "Niko, quên fact anh thích checklist màu xanh",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            facts = store.list_facts()

        self.assertTrue(result.handled)
        self.assertTrue(result.decision["loop_enabled"])
        self.assertIn(f"fact #{target}", result.reply)
        self.assertNotIn("nhiều fact", result.reply)
        self.assertEqual({fact.id for fact in facts}, {other_a, other_b})

    def test_runtime_loop_treats_weak_matches_as_no_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Anh", "Anh thích checklist có mục đích rõ ràng cho từng nhóm công việc.", source="test")
            second = store.add_fact(
                "Chat memory",
                "anh thích checklist có mục đích rõ ràng, chia theo phase, và có tiêu chí hoàn thành rõ ràng",
                source="test",
            )
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="anh thích bánh màu cầu vồng",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "Niko, quên fact anh thích bánh màu cầu vồng",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            facts = store.list_facts()
            clarify_events = [
                event
                for event in trace_logger.read_events()
                if event.get("kind") == "memory_correction_clarify"
            ]

        self.assertTrue(result.handled)
        self.assertIn("chưa tìm thấy fact nào", result.reply)
        self.assertNotIn("nhiều fact", result.reply)
        self.assertEqual({fact.id for fact in facts}, {first, second})
        self.assertEqual(clarify_events[-1]["data"]["clarify_reason"], "no_fact_match")

    def test_runtime_loop_ambiguous_result_keeps_pending_followup_in_v1(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            second = store.add_fact("Project memory", "checklist phase cần mục đích", source="test")
            trace_logger = self._trace(temp_dir)
            decider_calls = []

            def decide(prompt, _gateway):
                decider_calls.append(prompt)
                return MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist",
                    target_type=MEMORY_TARGET_FACT,
                )

            runtime = MemoryRuntime(store=store, correction_decider=decide)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                ambiguous = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
                followup = runtime.handle_memory_correction(
                    "456",
                    f"fact #{first} nhé",
                    self._message(),
                    "trace-2",
                    trace_logger,
                )
            facts = store.list_facts()
            events = trace_logger.read_events()

        self.assertTrue(ambiguous.handled)
        self.assertIn("nhiều fact", ambiguous.reply)
        self.assertTrue(ambiguous.decision["loop_enabled"])
        self.assertTrue(followup.handled)
        self.assertIn(f"fact #{first}", followup.reply)
        self.assertEqual([fact.id for fact in facts], [second])
        self.assertEqual(len(decider_calls), 1)
        self.assertIn("memory_correction_context_fallback", {event.get("kind") for event in events})

    def test_runtime_loop_pending_followup_survives_new_runtime_instance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            second = store.add_fact("Project memory", "checklist phase cần mục đích", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            def fail_if_called(_prompt, _gateway, _decision_context=None):
                raise AssertionError("pending follow-up should bypass correction model")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                ambiguous = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
                restarted_runtime = MemoryRuntime(store=store, correction_decider=fail_if_called)
                followup = restarted_runtime.handle_memory_correction(
                    "456",
                    f"fact #{first} nhé",
                    self._message(),
                    "trace-2",
                    trace_logger,
                )
            facts = store.list_facts()
            events = trace_logger.read_events()
            pending_after_followup = store.get_memory_correction_pending("456")

        self.assertTrue(ambiguous.handled)
        self.assertIn("nhiều fact", ambiguous.reply)
        self.assertTrue(followup.handled)
        self.assertIn(f"fact #{first}", followup.reply)
        self.assertEqual([fact.id for fact in facts], [second])
        self.assertIsNone(pending_after_followup)
        self.assertIn("memory_correction_pending_created", {event.get("kind") for event in events})
        self.assertIn("memory_correction_pending_resolved", {event.get("kind") for event in events})

    def test_runtime_loop_pending_followup_rejects_unoffered_fact_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            other = store.add_fact("Other memory", "anh thích bảng màu xanh", source="test")
            trace_logger = self._trace(temp_dir)
            store.set_memory_correction_pending(
                conversation_id="456",
                decision=MEMORY_FORGET_MEMORY,
                query="checklist",
                replacement="",
                fact_ids=[first],
                trace_id="trace-old",
                ttl_seconds=900,
            )
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: (_ for _ in ()).throw(
                    AssertionError("pending follow-up should bypass correction model")
                ),
            )

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    f"fact #{other} nhé",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            facts = store.list_facts()
            pending_after_followup = store.get_memory_correction_pending("456")

        self.assertTrue(result.handled)
        self.assertIn("không nằm trong danh sách", result.reply)
        self.assertEqual({fact.id for fact in facts}, {first, other})
        self.assertIsNotNone(pending_after_followup)

    def test_runtime_loop_pending_followup_expires_without_mutating_fact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            trace_logger = self._trace(temp_dir)
            store.set_memory_correction_pending(
                conversation_id="456",
                decision=MEMORY_FORGET_MEMORY,
                query="checklist",
                replacement="",
                fact_ids=[fact_id],
                trace_id="trace-old",
                ttl_seconds=-1,
            )
            runtime = MemoryRuntime(store=store)

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    f"fact #{fact_id} nhé",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            facts = store.list_facts()
            events = trace_logger.read_events()
            pending_after_followup = store.get_memory_correction_pending("456")

        self.assertTrue(result.handled)
        self.assertIn("hết hạn", result.reply)
        self.assertEqual([fact.id for fact in facts], [fact_id])
        self.assertIsNone(pending_after_followup)
        self.assertIn("memory_correction_pending_expired", {event.get("kind") for event in events})

    def test_runtime_loop_ambiguous_update_uses_durable_pending_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            first = store.add_fact("Chat memory", "anh thích checklist ngắn", source="test")
            second = store.add_fact("Project memory", "checklist phase cần mục đích", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_CORRECT_MEMORY,
                    query="checklist",
                    target_type=MEMORY_TARGET_FACT,
                    replacement="anh thích checklist có tiêu chí hoàn thành rõ ràng",
                ),
            )

            def fail_if_called(_prompt, _gateway, _decision_context=None):
                raise AssertionError("pending follow-up should bypass correction model")

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                ambiguous = runtime.handle_memory_correction(
                    "456",
                    "sửa fact checklist thành anh thích checklist có tiêu chí hoàn thành rõ ràng",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
                restarted_runtime = MemoryRuntime(store=store, correction_decider=fail_if_called)
                followup = restarted_runtime.handle_memory_correction(
                    "456",
                    f"fact #{first} nhé",
                    self._message(),
                    "trace-2",
                    trace_logger,
                )
            facts = {fact.id: fact for fact in store.list_facts()}

        self.assertTrue(ambiguous.handled)
        self.assertIn("nhiều fact", ambiguous.reply)
        self.assertTrue(followup.handled)
        self.assertIn(f"fact #{first}", followup.reply)
        self.assertIn("tiêu chí hoàn thành", facts[first].content)
        self.assertEqual(facts[second].content, "checklist phase cần mục đích")
        self.assertEqual(facts[first].meta["previous_content"], "anh thích checklist ngắn")

    def test_runtime_loop_error_falls_back_to_v1(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.sqlite3")
            fact_id = store.add_fact("Chat memory", "anh thích checklist rõ ràng", source="test")
            trace_logger = self._trace(temp_dir)
            runtime = MemoryRuntime(
                store=store,
                correction_decider=lambda _prompt, _gateway: MemoryCorrectionDecision(
                    decision=MEMORY_FORGET_MEMORY,
                    query="checklist rõ ràng",
                    target_type=MEMORY_TARGET_FACT,
                ),
            )

            def failing_loop(**_kwargs):
                raise RuntimeError("loop boom")

            runtime._correction_workflow._correction_loop_workflow.handle = failing_loop

            with patch.dict(
                os.environ,
                {
                    "NIKO_MEMORY_ENABLED": "1",
                    "NIKO_MEMORY_WRITE_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED": "1",
                    "NIKO_MEMORY_CORRECTION_LOOP_ENABLED": "1",
                },
                clear=False,
            ):
                result = runtime.handle_memory_correction(
                    "456",
                    "quên fact checklist rõ ràng",
                    self._message(),
                    "trace-1",
                    trace_logger,
                )
            facts = store.list_facts()
            events = trace_logger.read_events()

        self.assertTrue(result.handled)
        self.assertNotIn("loop_enabled", result.decision)
        self.assertIn(f"fact #{fact_id}", result.reply)
        self.assertEqual(facts, [])
        self.assertIn("memory_correction_loop_error", {event.get("kind") for event in events})


if __name__ == "__main__":
    unittest.main()
