import unittest

from bots.decision_model.client import (
    DecisionModelConfig,
    build_systemone_choice_payload,
    parse_keep_alive,
    parse_systemone_choice_response,
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

    def test_route_choice_aliases(self):
        self.assertEqual(normalize_route_choice("reply_now"), ROUTE_REPLY_NOW)
        self.assertEqual(normalize_route_choice("handoff"), ROUTE_SEND_TO_DEEP)


if __name__ == "__main__":
    unittest.main()
