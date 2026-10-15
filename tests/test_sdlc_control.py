import unittest

from niko.sdlc.client import (
    ROUTE_SDLC_APPROVE,
    ROUTE_SDLC_CONTINUE,
    ROUTE_SDLC_STATUS,
    ROUTE_SDLC_SYNC,
    SdlcResult,
)
from niko.sdlc.control import (
    PREFLIGHT_ACTION_NOT_ALLOWED,
    PREFLIGHT_BACKGROUND_PENDING,
    PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED,
    PREFLIGHT_CODE_REVIEW_REQUIRED,
    PREFLIGHT_EXECUTE_APPROVAL,
    PREFLIGHT_PHASE4_REQUIRED,
    PREFLIGHT_STATUS_ERROR,
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

    def test_sync_requires_code_review_state_and_advertised_action(self):
        accepted = evaluate_sdlc_preflight(
            ROUTE_SDLC_SYNC,
            _status(status="code_review_sync_failed", allowed_next_actions=["sync_run", "get_status"]),
        )
        wrong_phase = evaluate_sdlc_preflight(
            ROUTE_SDLC_SYNC,
            _status(status="implementation_ready", allowed_next_actions=["sync_run"]),
        )

        self.assertEqual(accepted.reason, PREFLIGHT_BACKGROUND_PENDING)
        self.assertEqual(wrong_phase.reason, PREFLIGHT_CODE_REVIEW_REQUIRED)

    def test_phase1_to_3_approval_executes_but_code_review_approval_stays_disabled(self):
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
        self.assertFalse(code_review.execute_now)
        self.assertEqual(code_review.reason, PREFLIGHT_CODE_REVIEW_APPROVAL_DISABLED)
        self.assertIn("chưa duyệt Code Review", preflight_notice(code_review))

    def test_code_review_approval_requires_matching_checkpoint(self):
        preflight = evaluate_sdlc_preflight(
            ROUTE_SDLC_APPROVE,
            _status(
                status="code_review_ready",
                pending_checkpoint={"type": "design_approval"},
                allowed_next_actions=["approve_checkpoint"],
            ),
        )

        self.assertFalse(preflight.execute_now)
        self.assertEqual(preflight.reason, PREFLIGHT_ACTION_NOT_ALLOWED)

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
