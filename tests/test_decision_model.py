import unittest
from unittest.mock import patch

from bots.decision_model.client import (
    ChoiceDecision,
    DecisionModelConfig,
    build_ollama_unload_payload,
    build_systemone_choice_payload,
    parse_keep_alive,
    parse_systemone_choice_response,
    unload_decision_model,
)
from bots.decision_model.sticker import (
    NO_STICKER_MOOD,
    available_moods_from_config,
    build_sticker_mood_criteria,
    normalize_sticker_mood,
)
from bots.decision_model.jira import (
    JIRA_ASK_FOR_ISSUE_KEY,
    JIRA_SKIP,
    JIRA_USE_TOOL,
    build_jira_gate_criteria,
    decide_jira_gate,
    normalize_jira_gate_choice,
)
from bots.decision_model.memory import (
    MEMORY_CORRECT_MEMORY,
    MEMORY_CORRECTION_NONE,
    MEMORY_DISCARD,
    MEMORY_EPISODIC_EVENT,
    MEMORY_FORGET_MEMORY,
    MEMORY_LIST_FACTS,
    MEMORY_RETRIEVE,
    MEMORY_RETRIEVAL_LIST,
    MEMORY_RETRIEVAL_NONE,
    MEMORY_RETRIEVAL_RECENT,
    MEMORY_RETRIEVAL_SEARCH,
    MEMORY_RECENT_EPISODES,
    MEMORY_REMEMBER,
    MEMORY_SEMANTIC_FACT,
    MEMORY_SKIP,
    build_memory_candidate_criteria,
    build_memory_retrieval_criteria,
    build_memory_write_criteria,
    build_memory_write_instructions,
    build_memory_write_state,
    apply_memory_correction_prompt_hint,
    apply_memory_retrieval_prompt_hint,
    decide_memory_correction_intent,
    decide_memory_retrieval,
    extract_memory_correction_query_from_prompt,
    extract_memory_correction_replacement_from_prompt,
    is_memory_readonly_prompt,
    memory_inventory_prompt_target,
    normalize_episode_retrieval_mode,
    normalize_fact_retrieval_mode,
    normalize_memory_candidate_choice,
    normalize_memory_retrieval_choice,
    normalize_memory_write_choice,
)
from bots.decision_model.triage import (
    ROUTE_REPLY_NOW,
    ROUTE_SEND_TO_DEEP,
    build_triage_criteria,
    normalize_route_choice,
)


class DecisionModelTests(unittest.TestCase):
    def test_keep_alive_minus_one_parses_as_number(self):
        self.assertEqual(parse_keep_alive("-1"), -1)

    def test_choice_payload_includes_keep_alive(self):
        payload = build_systemone_choice_payload(
            state={"prompt": "ping"},
            question_name="route",
            instructions="classify",
            criteria=build_triage_criteria(),
            config=DecisionModelConfig(
                base_url="http://localhost:11434",
                model="nimble",
                timeout_seconds=10,
                keep_alive=-1,
            ),
        )

        self.assertEqual(payload["model"], "nimble")
        self.assertEqual(payload["keep_alive"], -1)
        self.assertEqual(payload["questions"]["route"]["type"], "choice")

    def test_ollama_unload_payload_uses_keep_alive_zero(self):
        config = DecisionModelConfig(model="nimble")

        payload = build_ollama_unload_payload(config)

        self.assertEqual(payload["model"], "nimble")
        self.assertEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["prompt"], "")
        self.assertFalse(payload["stream"])

    def test_unload_decision_model_posts_to_ollama_generate(self):
        config = DecisionModelConfig(base_url="http://localhost:11434", model="nimble", timeout_seconds=10)

        with patch("bots.decision_model.client.post_json", return_value={"done": True}) as post_json:
            response = unload_decision_model(config=config, timeout_seconds=3)

        self.assertEqual(response, {"done": True})
        post_json.assert_called_once_with(
            "http://localhost:11434/api/generate",
            {"model": "nimble", "prompt": "", "stream": False, "keep_alive": 0},
            timeout_seconds=3,
        )

    def test_parse_systemone_choice_response(self):
        decision = parse_systemone_choice_response(
            {
                "model": "nimble",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "reply_now",
                        "confidence": 0.91,
                        "probabilities": {"reply_now": 0.91, "send_to_deep": 0.09},
                        "reason": "small talk",
                    }
                },
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
            "route",
        )

        self.assertEqual(decision.choice, "reply_now")
        self.assertEqual(decision.confidence, 0.91)
        self.assertEqual(decision.probabilities["send_to_deep"], 0.09)
        self.assertEqual(decision.model, "nimble")
        self.assertEqual(decision.extra["reason"], "small talk")

    def test_route_choice_aliases(self):
        self.assertEqual(normalize_route_choice("reply_now"), ROUTE_REPLY_NOW)
        self.assertEqual(normalize_route_choice("handoff"), ROUTE_SEND_TO_DEEP)

    def test_jira_gate_choice_aliases_and_criteria(self):
        criteria = build_jira_gate_criteria()

        self.assertIn(JIRA_USE_TOOL, criteria)
        self.assertIn(JIRA_ASK_FOR_ISSUE_KEY, criteria)
        self.assertIn(JIRA_SKIP, criteria)
        self.assertEqual(normalize_jira_gate_choice("use jira"), JIRA_USE_TOOL)
        self.assertEqual(normalize_jira_gate_choice("need_issue_key"), JIRA_ASK_FOR_ISSUE_KEY)
        self.assertEqual(normalize_jira_gate_choice("normal_chat"), JIRA_SKIP)

        with self.assertRaises(RuntimeError):
            normalize_jira_gate_choice("maybe")

    def test_jira_gate_decision_carries_metadata(self):
        with patch(
            "bots.decision_model.jira.systemone_choice",
            return_value=ChoiceDecision(
                choice=JIRA_USE_TOOL,
                confidence=0.91,
                probabilities={JIRA_USE_TOOL: 0.91, JIRA_SKIP: 0.09},
                model="nimble",
                usage={"input_tokens": 10},
                extra={"issue_key": "niko-101", "query": "ticket vừa nãy", "reason": "followup"},
            ),
        ):
            decision = decide_jira_gate(
                "xem ticket vừa nãy giúp anh",
                recent_turns=[{"role": "user", "content": "phân tích NIKO-101"}],
            )

        self.assertEqual(decision.decision, JIRA_USE_TOOL)
        self.assertEqual(decision.issue_key, "NIKO-101")
        self.assertEqual(decision.query, "ticket vừa nãy")
        self.assertEqual(decision.reason, "followup")
        self.assertEqual(decision.confidence, 0.91)
        self.assertEqual(decision.model, "nimble")

    def test_sticker_mood_criteria_always_includes_no_sticker(self):
        criteria = build_sticker_mood_criteria(["happy", "coding"])

        self.assertIn(NO_STICKER_MOOD, criteria)
        self.assertIn("happy", criteria)
        self.assertIn("coding", criteria)

    def test_sticker_mood_aliases_and_invalid_choices(self):
        self.assertEqual(normalize_sticker_mood("no sticker", ["happy"]), NO_STICKER_MOOD)
        self.assertEqual(normalize_sticker_mood("happy", ["happy"]), "happy")

        with self.assertRaises(RuntimeError):
            normalize_sticker_mood("angry", ["happy"])

    def test_available_sticker_moods_follow_config_priority(self):
        moods = available_moods_from_config(
            {
                "mood_priority": ["happy", "coding"],
                "moods": {"coding": {}, "happy": {}, "neutral": {}},
            }
        )

        self.assertEqual(moods, ["happy", "coding", "neutral"])

    def test_memory_retrieval_choice_aliases_and_criteria(self):
        criteria = build_memory_retrieval_criteria()

        self.assertIn(MEMORY_SKIP, criteria)
        self.assertIn(MEMORY_RETRIEVE, criteria)
        self.assertIn(MEMORY_LIST_FACTS, criteria)
        self.assertIn(MEMORY_RECENT_EPISODES, criteria)
        self.assertEqual(normalize_memory_retrieval_choice("read memory"), MEMORY_RETRIEVE)
        self.assertEqual(normalize_memory_retrieval_choice("no memory"), MEMORY_SKIP)
        self.assertEqual(normalize_memory_retrieval_choice("list facts"), MEMORY_RETRIEVE)
        self.assertEqual(normalize_memory_retrieval_choice("recent episodes"), MEMORY_RETRIEVE)

        with self.assertRaises(RuntimeError):
            normalize_memory_retrieval_choice("maybe")

    def test_memory_retrieval_inventory_choices_set_default_modes(self):
        with patch("bots.decision_model.memory.systemone_choice", return_value=ChoiceDecision(choice=MEMORY_LIST_FACTS)):
            fact_inventory = decide_memory_retrieval("Hien tai em dang luu nhung fact nao?")
        with patch(
            "bots.decision_model.memory.systemone_choice",
            return_value=ChoiceDecision(choice=MEMORY_RECENT_EPISODES),
        ):
            episode_inventory = decide_memory_retrieval("Gan day em nho nhung episode nao?")

        self.assertEqual(fact_inventory.decision, MEMORY_RETRIEVE)
        self.assertEqual(fact_inventory.fact_mode, MEMORY_RETRIEVAL_LIST)
        self.assertEqual(fact_inventory.episode_mode, MEMORY_RETRIEVAL_NONE)
        self.assertEqual(episode_inventory.decision, MEMORY_RETRIEVE)
        self.assertEqual(episode_inventory.fact_mode, MEMORY_RETRIEVAL_NONE)
        self.assertEqual(episode_inventory.episode_mode, MEMORY_RETRIEVAL_RECENT)

    def test_memory_retrieval_inventory_prompt_forces_list_mode_after_model(self):
        prompt = "Hiện tại em có những fact nào về anh?"

        with patch(
            "bots.decision_model.memory.systemone_choice",
            return_value=ChoiceDecision(
                choice=MEMORY_RETRIEVE,
                extra={"query": "fact", "fact_mode": "search", "episode_mode": "search"},
            ),
        ):
            decision = decide_memory_retrieval(prompt)

        self.assertEqual(memory_inventory_prompt_target(prompt), "facts")
        self.assertEqual(apply_memory_retrieval_prompt_hint(prompt, MEMORY_SKIP), MEMORY_SKIP)
        self.assertEqual(decision.decision, MEMORY_RETRIEVE)
        self.assertEqual(decision.query, "")
        self.assertEqual(decision.fact_mode, MEMORY_RETRIEVAL_LIST)
        self.assertEqual(decision.episode_mode, MEMORY_RETRIEVAL_NONE)
        self.assertEqual(decision.label, MEMORY_RETRIEVE)

    def test_memory_correction_readonly_inventory_forces_none(self):
        prompt = "Hiện tại em có những fact gì về anh?"

        with patch(
            "bots.decision_model.memory.systemone_choice",
            return_value=ChoiceDecision(choice=MEMORY_CORRECT_MEMORY, extra={"query": "fact"}),
        ):
            decision = decide_memory_correction_intent(prompt)

        self.assertTrue(is_memory_readonly_prompt(prompt))
        self.assertTrue(is_memory_readonly_prompt("Bộ nhớ hiện đang lưu gì về anh?"))
        self.assertEqual(apply_memory_correction_prompt_hint(prompt, MEMORY_CORRECT_MEMORY), MEMORY_CORRECTION_NONE)
        self.assertEqual(
            apply_memory_correction_prompt_hint("Bộ nhớ hiện đang lưu gì về anh?", MEMORY_FORGET_MEMORY),
            MEMORY_CORRECTION_NONE,
        )
        self.assertEqual(decision.decision, MEMORY_CORRECTION_NONE)
        self.assertEqual(decision.label, MEMORY_CORRECT_MEMORY)

    def test_memory_correction_extracts_replacement_when_model_mislabels_fix(self):
        prompt = (
            "sửa fact checklist có mục đích rõ ràng thành anh thích checklist có mục đích rõ ràng, "
            "chia theo phase, và có tiêu chí hoàn thành rõ ràng"
        )

        with patch(
            "bots.decision_model.memory.systemone_choice",
            return_value=ChoiceDecision(choice=MEMORY_FORGET_MEMORY),
        ):
            decision = decide_memory_correction_intent(prompt)

        expected = "anh thích checklist có mục đích rõ ràng, chia theo phase, và có tiêu chí hoàn thành rõ ràng"
        self.assertEqual(decision.decision, MEMORY_CORRECT_MEMORY)
        self.assertEqual(decision.label, MEMORY_FORGET_MEMORY)
        self.assertEqual(decision.replacement, expected)
        self.assertEqual(extract_memory_correction_replacement_from_prompt(prompt), expected)

    def test_memory_correction_extracts_query_when_model_omits_it(self):
        prompt = "Niko, quên fact anh thích checklist màu xanh"

        with patch(
            "bots.decision_model.memory.systemone_choice",
            return_value=ChoiceDecision(choice=MEMORY_FORGET_MEMORY),
        ):
            decision = decide_memory_correction_intent(prompt)

        self.assertEqual(decision.decision, MEMORY_FORGET_MEMORY)
        self.assertEqual(decision.query, "anh thích checklist màu xanh")
        self.assertEqual(extract_memory_correction_query_from_prompt(prompt), "anh thích checklist màu xanh")

    def test_memory_retrieval_modes_aliases_and_invalid_choices(self):
        self.assertEqual(normalize_fact_retrieval_mode("", default=MEMORY_RETRIEVAL_SEARCH), MEMORY_RETRIEVAL_SEARCH)
        self.assertEqual(normalize_fact_retrieval_mode("inventory", default=MEMORY_RETRIEVAL_SEARCH), MEMORY_RETRIEVAL_LIST)
        self.assertEqual(normalize_fact_retrieval_mode("no facts", default=MEMORY_RETRIEVAL_SEARCH), MEMORY_RETRIEVAL_NONE)
        self.assertEqual(normalize_episode_retrieval_mode("list", default=MEMORY_RETRIEVAL_SEARCH), MEMORY_RETRIEVAL_RECENT)
        self.assertEqual(
            normalize_episode_retrieval_mode("topic search", default=MEMORY_RETRIEVAL_SEARCH),
            MEMORY_RETRIEVAL_SEARCH,
        )

        with self.assertRaises(RuntimeError):
            normalize_fact_retrieval_mode("maybe", default=MEMORY_RETRIEVAL_SEARCH)
        with self.assertRaises(RuntimeError):
            normalize_episode_retrieval_mode("maybe", default=MEMORY_RETRIEVAL_SEARCH)

    def test_memory_write_choice_aliases_and_criteria(self):
        criteria = build_memory_write_criteria()

        self.assertIn(MEMORY_REMEMBER, criteria)
        self.assertIn(MEMORY_DISCARD, criteria)
        self.assertIn("inspection", criteria[MEMORY_DISCARD])
        self.assertEqual(normalize_memory_write_choice("save"), MEMORY_REMEMBER)
        self.assertEqual(normalize_memory_write_choice("write memory"), MEMORY_REMEMBER)
        self.assertEqual(normalize_memory_write_choice("no memory"), MEMORY_DISCARD)
        self.assertEqual(normalize_memory_write_choice("skip"), MEMORY_DISCARD)

        with self.assertRaises(RuntimeError):
            normalize_memory_write_choice("maybe")

    def test_memory_write_instructions_discard_memory_inventory_turns(self):
        instructions = build_memory_write_instructions()
        state = build_memory_write_state(
            "Hien tai em dang luu nhung fact nao ve anh?",
            "Em dang luu 2 fact ve anh.",
            route="deep_agent",
        )

        self.assertIn("memory inspection", instructions)
        self.assertIn("inventory", instructions)
        self.assertIn("listing memory is not itself a durable event", instructions)
        self.assertIn("inspects, lists, or confirms existing memory", state["decision_context"])

    def test_memory_candidate_choice_aliases_and_criteria(self):
        criteria = build_memory_candidate_criteria()

        self.assertIn(MEMORY_SEMANTIC_FACT, criteria)
        self.assertIn(MEMORY_EPISODIC_EVENT, criteria)
        self.assertIn(MEMORY_DISCARD, criteria)
        self.assertEqual(normalize_memory_candidate_choice("fact"), MEMORY_SEMANTIC_FACT)
        self.assertEqual(normalize_memory_candidate_choice("episode"), MEMORY_EPISODIC_EVENT)
        self.assertEqual(normalize_memory_candidate_choice("ignore"), MEMORY_DISCARD)

        with self.assertRaises(RuntimeError):
            normalize_memory_candidate_choice("maybe")


if __name__ == "__main__":
    unittest.main()
