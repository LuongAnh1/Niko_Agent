import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from niko.config import RUNTIME_CONFIG_ENV, update_runtime_config
from niko.sdlc.client import (
    ROUTE_SDLC_ANSWER_CLARIFICATION,
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_REVALIDATE_PLAN,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcResult,
)
from niko.sdlc.control import (
    PREFLIGHT_ACTION_NOT_ALLOWED,
    PREFLIGHT_BACKGROUND_PENDING,
    PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED,
    PREFLIGHT_EXECUTE_APPROVAL,
    PREFLIGHT_PHASE4_REQUIRED,
    PREFLIGHT_QUESTION_NOT_CURRENT,
    PREFLIGHT_STATUS_ERROR,
    code_review_approval_enabled,
    evaluate_sdlc_preflight,
    preflight_notice,
)


def _status(**overrides):
    values = {
        "ok": True,
        "action": ROUTE_SDLC_STATUS,
        "run_id": "run_20261015_120000_abcd1234",
        "current_phase": "implementation",
        "status": "implementation_ready",
        "allowed_next_actions": ["continue_run", "get_status"],
    }
    values.update(overrides)
    return SdlcResult(**values)


class SdlcPreflightTests(unittest.TestCase):
    def test_clarification_answer_requires_current_question_and_contract_action(self):
        clarification = _status(
            current_phase="requirement_clarification",
            status="waiting_requirement_clarification",
            pending_checkpoint={
                "type": "requirement_clarification",
                "current_question": {"id": "Q-4", "text": "Which fields define a duplicate?"},
            },
            allowed_next_actions=["answer_clarification", "get_status"],
        )

        accepted = evaluate_sdlc_preflight(
            ROUTE_SDLC_ANSWER_CLARIFICATION,
            clarification,
            question_id="q-4",
        )
        stale = evaluate_sdlc_preflight(
            ROUTE_SDLC_ANSWER_CLARIFICATION,
            clarification,
            question_id="Q-1",
        )
        missing_action = evaluate_sdlc_preflight(
            ROUTE_SDLC_ANSWER_CLARIFICATION,
            _status(
                current_phase="requirement_clarification",
                status="waiting_requirement_clarification",
                pending_checkpoint=clarification.pending_checkpoint,
                allowed_next_actions=["get_status"],
            ),
            question_id="Q-4",
        )

        self.assertTrue(accepted.allowed)
        self.assertFalse(accepted.execute_now)
        self.assertEqual(accepted.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertEqual(stale.reason, PREFLIGHT_QUESTION_NOT_CURRENT)
        self.assertIn("Q-4", preflight_notice(stale))
        self.assertEqual(missing_action.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_clarification_answer_requires_clarification_checkpoint_state(self):
        preflight = evaluate_sdlc_preflight(
            ROUTE_SDLC_ANSWER_CLARIFICATION,
            _status(
                status="waiting_requirement_approval",
                pending_checkpoint={
                    "type": "requirement_approval",
                    "current_question": {"id": "Q-4"},
                },
                allowed_next_actions=["answer_clarification"],
            ),
            question_id="Q-4",
        )

        self.assertFalse(preflight.allowed)
        self.assertEqual(preflight.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_continue_requires_phase4_and_advertised_action(self):
        accepted = evaluate_sdlc_preflight(ROUTE_SDLC_CONTINUE, _status())
        wrong_phase = evaluate_sdlc_preflight(
            ROUTE_SDLC_CONTINUE,
            _status(status="waiting_design_approval", allowed_next_actions=["continue_run"]),
        )
        missing_action = evaluate_sdlc_preflight(
            ROUTE_SDLC_CONTINUE,
            _status(allowed_next_actions=["get_status"]),
        )

        self.assertTrue(accepted.allowed)
        self.assertFalse(accepted.execute_now)
        self.assertEqual(accepted.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertEqual(wrong_phase.reason, PREFLIGHT_PHASE4_REQUIRED)
        self.assertEqual(missing_action.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_continue_accepts_tool_loop_progress_and_recoverable_phase4_block(self):
        executing = evaluate_sdlc_preflight(
            ROUTE_SDLC_CONTINUE,
            _status(status="implementation_paused", allowed_next_actions=["continue_run", "get_status"]),
        )
        legacy_block = evaluate_sdlc_preflight(
            ROUTE_SDLC_CONTINUE,
            _status(
                current_phase="implementation_writing",
                status="blocked",
                allowed_next_actions=["continue_run", "get_status"],
            ),
        )

        self.assertTrue(executing.allowed)
        self.assertEqual(executing.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertTrue(legacy_block.allowed)
        self.assertEqual(legacy_block.reason, PREFLIGHT_BACKGROUND_PENDING)

    def test_sync_uses_advertised_contract_action_across_checkpoint_phases(self):
        code_review = evaluate_sdlc_preflight(
            ROUTE_SDLC_SYNC,
            _status(status="code_review_sync_failed", allowed_next_actions=["sync_run", "get_status"]),
        )
        design_retry = evaluate_sdlc_preflight(
            ROUTE_SDLC_SYNC,
            _status(status="waiting_design_approval", allowed_next_actions=["approve_checkpoint", "sync_run"]),
        )
        missing_action = evaluate_sdlc_preflight(
            ROUTE_SDLC_SYNC,
            _status(status="waiting_design_approval", allowed_next_actions=["approve_checkpoint"]),
        )

        self.assertEqual(code_review.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertEqual(design_retry.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertEqual(missing_action.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_plan_revalidation_requires_advertised_contract_action(self):
        accepted = evaluate_sdlc_preflight(
            ROUTE_SDLC_REVALIDATE_PLAN,
            _status(
                current_phase="implementation_planning",
                status="waiting_implementation_plan_review",
                allowed_next_actions=["revalidate_implementation_plan", "get_status"],
            ),
        )
        rejected = evaluate_sdlc_preflight(
            ROUTE_SDLC_REVALIDATE_PLAN,
            _status(
                current_phase="implementation_planning",
                status="waiting_implementation_plan_review",
                allowed_next_actions=["get_status"],
            ),
        )

        self.assertEqual(accepted.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertFalse(accepted.execute_now)
        self.assertEqual(rejected.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_phase1_to_3_approval_executes_but_code_review_gate_defaults_off(self):
        design = evaluate_sdlc_preflight(
            ROUTE_SDLC_APPROVE,
            _status(
                status="waiting_design_approval",
                pending_checkpoint={"type": "design_approval"},
                allowed_next_actions=["approve_checkpoint", "get_status"],
            ),
        )
        code_review = evaluate_sdlc_preflight(
            ROUTE_SDLC_APPROVE,
            _status(
                status="code_review_ready",
                pending_checkpoint={"type": "code_review"},
                allowed_next_actions=["approve_checkpoint", "get_status"],
            ),
        )

        self.assertTrue(design.execute_now)
        self.assertEqual(design.reason, PREFLIGHT_EXECUTE_APPROVAL)
        self.assertFalse(code_review.allowed)
        self.assertFalse(code_review.execute_now)
        self.assertEqual(code_review.reason, PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED)
        self.assertIn("chưa duyệt Code Review", preflight_notice(code_review))

    def test_code_review_approval_is_background_when_gate_is_enabled(self):
        preflight = evaluate_sdlc_preflight(
            ROUTE_SDLC_APPROVE,
            _status(
                status="code_review_ready",
                pending_checkpoint={"type": "code_review"},
                allowed_next_actions=["approve_checkpoint", "get_status"],
            ),
            allow_code_review_approval=True,
        )

        self.assertTrue(preflight.allowed)
        self.assertFalse(preflight.execute_now)
        self.assertEqual(preflight.reason, PREFLIGHT_BACKGROUND_PENDING)

    def test_code_review_approval_requires_matching_checkpoint(self):
        preflight = evaluate_sdlc_preflight(
            ROUTE_SDLC_APPROVE,
            _status(
                status="code_review_ready",
                pending_checkpoint={"type": "design_approval"},
                allowed_next_actions=["approve_checkpoint"],
            ),
            allow_code_review_approval=True,
        )

        self.assertFalse(preflight.allowed)
        self.assertFalse(preflight.execute_now)
        self.assertEqual(preflight.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

    def test_code_review_approval_gate_reads_runtime_config_flag(self):
        with patch.dict("os.environ", {"NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": "0"}, clear=False):
            self.assertFalse(code_review_approval_enabled())
        with patch.dict("os.environ", {"NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": "1"}, clear=False):
            self.assertTrue(code_review_approval_enabled())

    def test_code_review_approval_gate_reads_dashboard_runtime_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            with patch.dict(os.environ, {RUNTIME_CONFIG_ENV: str(config_path)}, clear=False):
                os.environ.pop("NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED", None)
                update_runtime_config({"NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED": True})

                self.assertTrue(code_review_approval_enabled())

    def test_status_error_stops_preflight(self):
        preflight = evaluate_sdlc_preflight(
            ROUTE_SDLC_CONTINUE,
            _status(ok=False, error_code="run_not_found", message="Run not found"),
        )

        self.assertFalse(preflight.allowed)
        self.assertFalse(preflight.execute_now)
        self.assertEqual(preflight.reason, PREFLIGHT_STATUS_ERROR)
        self.assertEqual(preflight.status_result.error_code, "run_not_found")


if __name__ == "__main__":
    unittest.main()
