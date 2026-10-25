import unittest
from unittest.mock import MagicMock

from niko.sdlc.client import (
    ROUTE_SDLC_ANSWER_CLARIFICATION,
    ROUTE_SDLC_CHALLENGE_ASSUMPTION,
    ROUTE_SDLC_CLARIFY_MORE,
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_REVALIDATE,
    ROUTE_SDLC_REVALIDATE_DESIGN,
    ROUTE_SDLC_REVALIDATE_REQUIREMENT,
    ROUTE_SDLC_REVALIDATE_PLAN,
    ROUTE_SDLC_START,
    ROUTE_SDLC_START_JIRA,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcClient,
    detect_sdlc_request,
)


class SdlcClientTests(unittest.TestCase):
    def test_detects_explicit_agent_assumption_challenge(self):
        run_id = "run_20261012_120000_abcd1234"

        for prompt in (
            f"Phản đối giả định asm-1 cho {run_id}: Hãy hỏi người dùng thay vì tự chọn.",
            f"phan doi gia dinh ASM-1 cho {run_id}: Can hoi lai owner.",
            f"challenge assumption ASM-1 for {run_id}: Ask the owner instead.",
        ):
            with self.subTest(prompt=prompt):
                request = detect_sdlc_request(prompt)
                self.assertIsNotNone(request)
                self.assertEqual(request.action, ROUTE_SDLC_CHALLENGE_ASSUMPTION)
                self.assertEqual(request.run_id, run_id)
                self.assertEqual(request.assumption_id, "ASM-1")
                self.assertTrue(request.instruction)

    def test_incomplete_assumption_challenge_stays_on_safe_control_route(self):
        request = detect_sdlc_request("Phản đối giả định ASM-1: hỏi lại người dùng")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_CHALLENGE_ASSUMPTION)
        self.assertFalse(request.run_id and request.assumption_id and request.instruction)

    def test_detects_explicit_requirement_clarification_answer(self):
        request = detect_sdlc_request(
            "Trả lời Q-4 cho run_20261012_120000_abcd1234: Dùng mã địa điểm và tên chuẩn hóa để phát hiện trùng."
        )

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_ANSWER_CLARIFICATION)
        self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")
        self.assertEqual(request.question_id, "Q-4")
        self.assertEqual(request.answer, "Dùng mã địa điểm và tên chuẩn hóa để phát hiện trùng.")

    def test_clarification_answer_has_precedence_over_approval_words(self):
        request = detect_sdlc_request(
            "Trả lời q-4 cho run_20261012_120000_abcd1234: Anh đồng ý dùng chính sách này và approve kết quả."
        )

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_ANSWER_CLARIFICATION)
        self.assertEqual(request.question_id, "Q-4")

    def test_incomplete_clarification_syntax_does_not_fall_through_to_other_control_routes(self):
        prompts = (
            "Trả lời Q-4: đồng ý",
            "Trả lời Q-4 cho run_20261012_120000_abcd1234",
            "Trả lời cho run_20261012_120000_abcd1234: approve",
            "Trả lời Q-4 cho run_20261012_120000_abcd1234:   ",
        )

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIsNone(detect_sdlc_request(prompt))

    def test_detects_etour_uc1_start_request(self):
        request = detect_sdlc_request("Niko, thuc hien eTour UC1 giup anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_START)
        self.assertEqual(request.dataset, "eTour")
        self.assertEqual(request.use_case, "UC1")

    def test_detects_run_status_request(self):
        request = detect_sdlc_request("run_20261012_120000_abcd1234 den dau roi em?")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_STATUS)
        self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")

    def test_detects_checkpoint_approval_request(self):
        request = detect_sdlc_request("Niko, duyet run_20261012_120000_abcd1234 giup anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_APPROVE)
        self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")

    def test_detects_continue_and_sync_requests(self):
        cases = [
            ("chạy tiếp run_20261012_120000_abcd1234", ROUTE_SDLC_CONTINUE),
            ("continue run_20261012_120000_abcd1234", ROUTE_SDLC_CONTINUE),
            ("đồng bộ lại run_20261012_120000_abcd1234", ROUTE_SDLC_SYNC),
            ("retry sync run_20261012_120000_abcd1234", ROUTE_SDLC_SYNC),
        ]

        for prompt, expected_action in cases:
            with self.subTest(prompt=prompt):
                request = detect_sdlc_request(prompt)
                self.assertIsNotNone(request)
                self.assertEqual(request.action, expected_action)
                self.assertEqual(request.run_id, "run_20261012_120000_abcd1234")

    def test_detects_plan_revalidation_before_phase4_revalidation(self):
        run_id = "run_20261012_120000_abcd1234"

        plan_request = detect_sdlc_request(f"rà soát lại implementation plan {run_id}")
        code_request = detect_sdlc_request(f"chạy lại validator {run_id}")

        self.assertEqual(plan_request.action, ROUTE_SDLC_REVALIDATE_PLAN)
        self.assertEqual(code_request.action, ROUTE_SDLC_REVALIDATE)

    def test_detects_design_revalidation_before_generic_validator_request(self):
        run_id = "run_20261012_120000_abcd1234"

        request = detect_sdlc_request(f"revalidate design {run_id}")

        self.assertEqual(request.action, ROUTE_SDLC_REVALIDATE_DESIGN)
        self.assertEqual(request.run_id, run_id)

    def test_detects_requirement_revalidation_before_generic_validator_request(self):
        run_id = "run_20261012_120000_abcd1234"

        request = detect_sdlc_request(f"rà soát lại requirement {run_id}")

        self.assertEqual(request.action, ROUTE_SDLC_REVALIDATE_REQUIREMENT)
        self.assertEqual(request.run_id, run_id)

    def test_detects_explicit_clarify_more_separately_from_revalidation(self):
        run_id = "run_20261012_120000_abcd1234"

        request = detect_sdlc_request(f"làm rõ thêm requirement {run_id}")

        self.assertEqual(request.action, ROUTE_SDLC_CLARIFY_MORE)
        self.assertEqual(request.run_id, run_id)

    def test_control_intents_require_explicit_run_id(self):
        for prompt in ("chạy tiếp giúp anh", "đồng bộ lại giúp anh", "status giúp anh", "duyệt giúp anh"):
            with self.subTest(prompt=prompt):
                self.assertIsNone(detect_sdlc_request(prompt))

    def test_control_intent_precedence_is_fixed(self):
        run_id = "run_20261012_120000_abcd1234"
        cases = [
            (f"duyệt rồi đồng bộ lại và chạy tiếp {run_id}", ROUTE_SDLC_APPROVE),
            (f"đồng bộ lại rồi chạy tiếp và xem status {run_id}", ROUTE_SDLC_SYNC),
            (f"chạy tiếp rồi xem status {run_id}", ROUTE_SDLC_CONTINUE),
        ]

        for prompt, expected_action in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(detect_sdlc_request(prompt).action, expected_action)

    def test_detects_jira_issue_start_request(self):
        request = detect_sdlc_request("Niko, thực hiện issue SDLC-1 giúp anh")

        self.assertIsNotNone(request)
        self.assertEqual(request.action, ROUTE_SDLC_START_JIRA)
        self.assertEqual(request.issue_key, "SDLC-1")

    def test_ignores_unrelated_prompt(self):
        self.assertIsNone(detect_sdlc_request("hom nay bot co on khong em?"))

    def test_client_normalizes_api_status_payload(self):
        transport = MagicMock()
        transport.get_status.return_value = {
            "ok": True,
            "run_id": "run_20261012_120000_abcd1234",
            "source_ref": "jira_issue:SDLC-1",
            "status": "waiting_design_approval",
            "workspace": {
                "branch": "feature/sdlc-1-abcd1234",
                "pr_url": "https://github.test/example/target/pull/1",
            },
            "allowed_next_actions": ["approve_checkpoint", "get_status"],
        }

        result = SdlcClient(transport=transport).get_status(
            run_id="run_20261012_120000_abcd1234"
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.source_ref, "jira_issue:SDLC-1")
        self.assertEqual(result.status, "waiting_design_approval")
        self.assertEqual(result.workspace["branch"], "feature/sdlc-1-abcd1234")
        self.assertEqual(
            result.allowed_next_actions,
            ["approve_checkpoint", "get_status"],
        )
        transport.get_status.assert_called_once_with(
            run_id="run_20261012_120000_abcd1234"
        )

    def test_client_normalizes_invalid_optional_objects_to_empty_dicts(self):
        for value in (None, "invalid", []):
            with self.subTest(value=value):
                transport = MagicMock()
                transport.get_status.return_value = {
                    "ok": True,
                    "status": "done",
                    "workspace": value,
                    "last_error": value,
                }

                result = SdlcClient(transport=transport).get_status(
                    run_id="run_20261012_120000_abcd1234"
                )

                self.assertEqual(result.workspace, {})
                self.assertEqual(result.last_error, {})

    def test_client_preserves_api_business_error(self):
        transport = MagicMock()
        transport.run_action.return_value = {
            "ok": False,
            "run_id": "run_20261012_120000_abcd1234",
            "error_code": "checkpoint_not_current",
            "message": "Checkpoint is no longer current.",
        }

        result = SdlcClient(transport=transport).approve_checkpoint(
            run_id="run_20261012_120000_abcd1234",
            requested_by="telegram:1",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "checkpoint_not_current")
        self.assertEqual(result.message, "Checkpoint is no longer current.")
        transport.run_action.assert_called_once_with(
            run_id="run_20261012_120000_abcd1234",
            action="approve_checkpoint",
            requested_by="telegram:1",
            event=ROUTE_SDLC_APPROVE,
        )


if __name__ == "__main__":
    unittest.main()
