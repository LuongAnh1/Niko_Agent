import unittest

from niko.sdlc.client import ROUTE_SDLC_STATUS, SdlcResult
from niko.sdlc.formatting import format_sdlc_reply


def _result(**overrides):
    values = {
        "ok": True,
        "action": ROUTE_SDLC_STATUS,
        "run_id": "run_20261013_160421_f661babf",
        "source_ref": "jira_issue:SDLC-1",
        "current_phase": "code_review",
        "status": "code_review_ready",
        "allowed_next_actions": ["approve_checkpoint", "get_status"],
    }
    values.update(overrides)
    return SdlcResult(**values)


class SdlcFormatterPhase4Tests(unittest.TestCase):
    def test_formats_every_phase4_status(self):
        expected = {
            "implementation_ready": "sẵn sàng bắt đầu triển khai code",
            "implementation_writing": "đang viết và chỉnh sửa code",
            "implementation_written": "đã tạo code",
            "implementation_testing": "đang chạy compile và unit verification",
            "implementation_tested": "verification đã pass",
            "implementation_repair_needed": "Validator yêu cầu sửa code hoặc test",
            "implementation_repair_blocked": "repair implementation đang bị chặn",
            "implementation_traced": "verification và traceability đã pass",
            "qa_testing": "đang chạy QA acceptance",
            "qa_repair_needed": "QA phát hiện lỗi hành vi",
            "qa_tested": "QA đã pass",
            "code_review_publishing": "đang tạo commit, branch và Draft PR",
            "code_review_publish_failed": "xuất bản Draft PR bị lỗi",
            "code_review_published": "Draft PR đã được xuất bản",
            "code_review_syncing": "đang đồng bộ checkpoint Code Review",
            "code_review_sync_failed": "đồng bộ checkpoint đang lỗi",
            "code_review_ready": "Draft PR và evidence đã sẵn sàng",
            "code_review_approval_syncing": "PR đã chuyển sang Ready for review",
            "code_review_approval_sync_failed": "PR vẫn ở Ready for review",
            "code_review_approved": "anh tiếp tục review và merge trên GitHub",
        }

        for status, label in expected.items():
            with self.subTest(status=status):
                reply = format_sdlc_reply(_result(status=status, current_phase=status, allowed_next_actions=[]))
                self.assertIn(label, reply)

    def test_pr_url_fallback_sources(self):
        cases = [
            ({"pending_checkpoint": {"pull_request_url": "https://github.test/checkpoint"}}, "checkpoint"),
            ({"workspace": {"pr_url": "https://github.test/workspace"}}, "workspace"),
            (
                {"quality_reports": {"code_review_publication": {"pr_url": "https://github.test/publication"}}},
                "publication",
            ),
            (
                {"quality_reports": {"code_review_approval": {"pr_url": "https://github.test/approval"}}},
                "approval",
            ),
            (
                {"last_sync": {"targets": [{"tool": "github", "url": "https://github.test/sync"}]}},
                "sync",
            ),
        ]
        for fields, suffix in cases:
            with self.subTest(source=suffix):
                reply = format_sdlc_reply(_result(**fields))
                self.assertIn(f"Draft PR: https://github.test/{suffix}", reply)

    def test_pr_url_uses_contract_priority(self):
        reply = format_sdlc_reply(
            _result(
                pending_checkpoint={"pull_request_url": "https://github.test/checkpoint"},
                workspace={"pr_url": "https://github.test/workspace"},
                quality_reports={"code_review_publication": {"pr_url": "https://github.test/publication"}},
                last_sync={"targets": [{"tool": "github", "url": "https://github.test/sync"}]},
            )
        )

        self.assertIn("Draft PR: https://github.test/checkpoint", reply)
        self.assertNotIn("Draft PR: https://github.test/workspace", reply)

    def test_workspace_and_artifacts_only_expose_safe_metadata(self):
        reply = format_sdlc_reply(
            _result(
                summary_for_user=r"Report at D:\private\summary.json token=do-not-show",
                workspace={
                    "branch": "sdlc/run-demo",
                    "head_sha": "0123456789abcdef0123456789abcdef01234567",
                    "lifecycle": "published",
                    "pr_number": 27,
                    "worktree_path": r"D:\private\run-demo",
                    "remote": "secret-origin",
                    "pr_url": r"D:\private\not-a-url",
                },
                latest_artifacts={
                    "code_change_report": r"D:\private\code.json",
                    "test_report": r"D:\private\test.json",
                    "requirement_design_code_trace": r"D:\private\trace.json",
                    "qa_test_report": r"D:\private\qa.json",
                    "github_code_review": r"D:\private\github.json",
                    "run_workspace": r"D:\private\workspace.json",
                    "developer_repair_raw_response": "raw-secret",
                },
            )
        )

        self.assertIn("branch `sdlc/run-demo`", reply)
        self.assertIn("HEAD `0123456789ab`", reply)
        self.assertIn("PR `#27`", reply)
        self.assertIn("code change report", reply)
        self.assertIn("verification test report", reply)
        self.assertIn("requirement-design-code trace", reply)
        self.assertIn("QA test report", reply)
        self.assertIn("GitHub code review", reply)
        self.assertIn("run workspace", reply)
        self.assertNotIn(r"D:\private", reply)
        self.assertNotIn("secret-origin", reply)
        self.assertNotIn("raw-secret", reply)
        self.assertNotIn("do-not-show", reply)
        self.assertIn("token=[redacted]", reply)
        self.assertNotIn("raw response", reply.lower())

    def test_combines_phase4_quality_without_dumping_reports(self):
        reply = format_sdlc_reply(
            _result(
                latest_artifacts={"test_report": "private-path"},
                quality_reports={
                    "implementation_traceability": {
                        "ok": True,
                        "summary": "All required links are present.",
                        "stats": {"mapping_count": 12},
                        "missing_count": 0,
                        "llm_confidence": 0.94,
                    },
                    "qa_acceptance": {
                        "ok": True,
                        "status": "passed",
                        "summary": "Acceptance scenarios passed.",
                        "scenario_count": 6,
                        "risk_count": 1,
                    },
                    "code_review_publication": {
                        "ok": True,
                        "draft": True,
                        "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
                    },
                },
            )
        )

        self.assertIn("Verification: passed", reply)
        self.assertIn("Traceability: passed; mappings=12; missing=0; confidence=0.94", reply)
        self.assertIn("QA: passed; scenarios=6; risks=1", reply)
        self.assertIn("GitHub: PR Draft; commit=abcdef123456", reply)
        self.assertNotIn("private-path", reply)

    def test_repair_states_do_not_claim_failed_stage_passed(self):
        implementation_reply = format_sdlc_reply(
            _result(
                status="implementation_repair_needed",
                latest_artifacts={"test_report": "path"},
                quality_reports={
                    "implementation_traceability": {"ok": False, "missing_count": 2},
                    "implementation_repair": {"needed": True, "summary": "Repair two missing links."},
                },
                allowed_next_actions=["continue_run"],
            )
        )
        qa_reply = format_sdlc_reply(
            _result(
                status="qa_repair_needed",
                latest_artifacts={"test_report": "path"},
                quality_reports={"qa_acceptance": {"ok": False, "status": "failed", "risk_count": 2}},
                allowed_next_actions=["continue_run"],
            )
        )

        self.assertNotIn("Verification: passed", implementation_reply)
        self.assertIn("Traceability: chưa pass", implementation_reply)
        self.assertIn("QA: failed", qa_reply)
        self.assertNotIn("QA: passed", qa_reply)

    def test_sync_failure_shows_error_and_failed_target(self):
        reply = format_sdlc_reply(
            _result(
                status="code_review_sync_failed",
                last_error={"error_code": "confluence_sync_failed", "message": "API timeout"},
                last_sync={
                    "status": "partial_failed",
                    "targets": [
                        {"tool": "jira", "status": "synced", "url": "https://jira.test/SDLC-1"},
                        {"tool": "confluence", "status": "failed", "error": "page update timed out"},
                    ],
                },
                allowed_next_actions=["sync_run", "get_status"],
            )
        )

        self.assertIn("Đồng bộ một phần:", reply)
        self.assertIn("Confluence: failed", reply)
        self.assertIn("confluence_sync_failed", reply)
        self.assertIn("retry đồng bộ", reply)

    def test_code_review_ready_keeps_pr_draft_and_merge_human_owned(self):
        reply = format_sdlc_reply(
            _result(
                pending_checkpoint={
                    "type": "code_review",
                    "pull_request_url": "https://github.test/pr/27",
                }
            )
        )

        self.assertIn("Draft PR: https://github.test/pr/27", reply)
        self.assertIn("Checkpoint cần anh duyệt: Code Review", reply)
        self.assertIn("merge vẫn do anh thực hiện", reply)
        self.assertNotIn("auto merge", reply.lower())

    def test_approval_sync_failure_only_suggests_sync_retry(self):
        reply = format_sdlc_reply(
            _result(
                status="code_review_approval_sync_failed",
                workspace={"pr_url": "https://github.test/pr/27"},
                allowed_next_actions=["approve_checkpoint", "sync_run", "get_status"],
                last_error={"error_code": "jira_sync_failed", "message": "Jira unavailable"},
            )
        )

        self.assertIn("PR Ready for review", reply)
        self.assertIn("Retry đồng bộ", reply)
        self.assertIn("không chạy lại GitHub approval", reply)
        self.assertNotIn("duyệt checkpoint", reply)

    def test_approved_state_leaves_review_and_merge_to_human(self):
        reply = format_sdlc_reply(
            _result(
                status="code_review_approved",
                workspace={"pr_url": "https://github.test/pr/27"},
                allowed_next_actions=["get_status"],
            )
        )

        self.assertIn("PR Ready for review", reply)
        self.assertIn("review PR và quyết định merge trực tiếp trên GitHub", reply)

    def test_malformed_optional_fields_and_long_text_are_safe(self):
        reply = format_sdlc_reply(
            _result(
                summary_for_user="x" * 1000,
                pending_checkpoint="bad",
                workspace=["bad"],
                latest_artifacts=["bad"],
                quality_reports=["bad"],
                last_sync=["bad"],
            )
        )

        self.assertIn("…", reply)
        self.assertLess(len(reply), 1200)


if __name__ == "__main__":
    unittest.main()
