import tempfile
import unittest
from pathlib import Path

from niko.loop import LoopDecision, LoopRuntime, ToolContext, ToolRegistry
from niko.memory.loop_tools import build_memory_fact_tools
from niko.memory.store import MemoryStore


class MemoryLoopToolsTests(unittest.TestCase):
    def test_search_facts_returns_matching_fact_dicts(self):
        with self._store() as store:
            fact_id = store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            result = registry.execute("search_facts", {"query": "checklist xanh", "top_k": 3}, ToolContext())

        self.assertTrue(result.ok)
        self.assertEqual(result.data["facts"][0]["id"], fact_id)
        self.assertIn("checklist", result.text)

    def test_list_facts_returns_newest_facts_and_limit(self):
        with self._store() as store:
            older_id = store.add_fact("Old", "fact cũ")
            newer_id = store.add_fact("New", "fact mới")
            registry = self._registry(store)

            result = registry.execute("list_facts", {"limit": 1}, ToolContext())

        self.assertTrue(result.ok)
        self.assertEqual([fact["id"] for fact in result.data["facts"]], [newer_id])
        self.assertNotEqual(result.data["facts"][0]["id"], older_id)

    def test_update_fact_changes_subject_and_content(self):
        with self._store() as store:
            fact_id = store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            result = registry.execute(
                "update_fact",
                {"fact_id": fact_id, "subject": "Preference", "content": "anh thích checklist màu tím"},
                ToolContext(trace_id="trace-1"),
            )
            facts = store.search_facts("màu tím", top_k=3)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["fact_id"], fact_id)
        self.assertEqual(facts[0].content, "anh thích checklist màu tím")
        self.assertEqual(facts[0].meta["previous_content"], "anh thích checklist màu xanh")
        self.assertEqual(facts[0].meta["corrected_by"], "loop_tool")
        self.assertEqual(facts[0].meta["correction_trace_id"], "trace-1")

    def test_delete_fact_removes_fact(self):
        with self._store() as store:
            fact_id = store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            result = registry.execute("delete_fact", {"fact_id": fact_id}, ToolContext())
            facts = store.list_facts(limit=10)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["fact_id"], fact_id)
        self.assertEqual(facts, [])

    def test_update_and_delete_fail_closed_for_missing_or_invalid_input(self):
        with self._store() as store:
            fact_id = store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            missing_id = registry.execute(
                "update_fact",
                {"fact_id": 999, "subject": "Preference", "content": "nội dung mới"},
                ToolContext(),
            )
            empty_content = registry.execute(
                "update_fact",
                {"fact_id": fact_id, "subject": "", "content": "nội dung mới"},
                ToolContext(),
            )
            invalid_delete = registry.execute("delete_fact", {"fact_id": "abc"}, ToolContext())
            facts = store.list_facts(limit=10)

        self.assertFalse(missing_id.ok)
        self.assertEqual(missing_id.error, "fact_not_found")
        self.assertFalse(empty_content.ok)
        self.assertEqual(empty_content.error, "fact_content_required")
        self.assertFalse(invalid_delete.ok)
        self.assertEqual(invalid_delete.error, "fact_id_required")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].id, fact_id)

    def test_loop_runtime_can_search_facts_then_final(self):
        with self._store() as store:
            store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            def controller(prompt, context, history, tools):
                if not history:
                    return LoopDecision.tool("search_facts", {"query": "checklist xanh"})
                return LoopDecision.final(f"found {len(history[-1]['data']['facts'])} fact")

            result = LoopRuntime(controller, registry).run("tìm fact checklist", ToolContext())

        self.assertEqual(result.reply, "found 1 fact")
        self.assertEqual(result.tool_calls[0]["tool_name"], "search_facts")
        self.assertFalse(result.tool_calls[0]["mutates_state"])

    def test_loop_runtime_marks_delete_fact_as_mutating_tool_call(self):
        with self._store() as store:
            fact_id = store.add_fact("Preference", "anh thích checklist màu xanh")
            registry = self._registry(store)

            def controller(prompt, context, history, tools):
                if not history:
                    return LoopDecision.tool("delete_fact", {"fact_id": fact_id})
                return LoopDecision.final("deleted")

            result = LoopRuntime(controller, registry).run("xóa fact", ToolContext())

        self.assertEqual(result.reply, "deleted")
        self.assertTrue(result.tool_calls[0]["mutates_state"])
        self.assertTrue(result.tool_calls[0]["ok"])

    @staticmethod
    def _registry(store):
        return ToolRegistry(build_memory_fact_tools(store_provider=lambda: store))

    @staticmethod
    def _store():
        temp_dir = tempfile.TemporaryDirectory()
        store = MemoryStore(Path(temp_dir.name) / "memory.sqlite3")

        class StoreContext:
            def __enter__(self):
                return store

            def __exit__(self, exc_type, exc, traceback):
                temp_dir.cleanup()

        return StoreContext()


if __name__ == "__main__":
    unittest.main()
