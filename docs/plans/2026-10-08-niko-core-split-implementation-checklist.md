# Checklist Triển Khai Tách Core, Gateway Và Graph Cho Niko

Ngày lập: 2026-10-08
Tài liệu gốc: `docs/plans/2026-10-08-niko-core-split-implementation-plan.md`
Trạng thái: đang triển khai Phase 1 Gateway Runner khung

## Phase 0: Khóa Tài Liệu Triển Khai

Mục đích: đảm bảo trước khi refactor đã có plan, checklist và docs trỏ đúng về
nguồn sự thật mới.

- [x] Tạo implementation plan cho core/gateway/graph split.
- [x] Tạo checklist để tick từng phần trong giai đoạn tách.
- [x] Cập nhật `docs/plans/README.md`.
- [x] Cập nhật `AGENTS.md` để session sau biết plan triển khai chính thức.
- [x] Cập nhật docs kiến trúc liên quan để trỏ tới implementation plan.
- [x] Rà `rtk rg` cho các cụm `core split`, `gateway runner`, `NikoApp`,
      `TurnOrchestrator`, `ChatReplyGraph`, `tool router`.
- [x] Chạy `rtk git diff --check` cho thay đổi docs-only.

## Phase 1: Gateway Runner Khung

Mục đích: tạo lớp gọi chung cho gateway nhưng chưa đổi behavior chat.

- [x] Tạo package `niko/gateway/`.
- [x] Tạo runner nhận `ChatGatewayMessage`, `deliver_reply`, `notify_working`.
- [x] Runner gọi `ChatReplyGraph.handle_message(...)` như hiện tại.
- [x] Telegram bot chuyển sang gọi runner.
- [x] Giữ nguyên route labels, trace events và runtime logs hiện có.
- [x] Thêm hoặc cập nhật tests cho runner path.
- [x] Chạy targeted Telegram tests.

Kết quả kiểm thử 2026-10-08:

- `rtk proxy python -m pytest tests/test_gateway_runner.py tests/test_telegram_prompt.py tests/test_ops_dashboard.py -q`
  pass `59 passed`.
- `rtk proxy python -m pytest -q` pass `210 passed`.
- `rtk proxy git diff --check` pass, chỉ có warning CRLF do cấu hình Git/Windows.

## Phase 2: NikoApp Assembly Root

Mục đích: gom wiring app vào một chỗ, không để gateway tự biết graph/runtime.

- [ ] Tạo `NikoApp` hoặc `create_niko_app()`.
- [ ] App quản lý MemoryStore/MemoryRuntime/TraceLogger/ChatReplyGraph.
- [ ] App có chỗ inject Jira workflow và dependency sau này.
- [ ] Gateway runner nhận app instance.
- [ ] Dashboard/bot startup dùng app factory thống nhất.
- [ ] Tests vẫn inject được store/logger tạm.
- [ ] Chạy Telegram + ops dashboard tests.

## Phase 3: TurnOrchestrator V0

Mục đích: tạo lớp quyết định workflow cấp turn, chuẩn bị tháo logic khỏi chat graph.

- [ ] Tạo package `niko/orchestration/` hoặc tên tương đương đã chốt.
- [ ] Tạo `TurnOrchestrator` nhận prompt, gateway message, callbacks và app deps.
- [ ] Orchestrator giữ thứ tự xử lý hiện tại.
- [ ] Busy/followup behavior vẫn không tạo duplicate Deep job.
- [ ] Normal chat fallback vẫn đi qua `ChatReplyGraph`.
- [ ] Trace `turn_start`/`turn_end` vẫn nhất quán.
- [ ] Chạy targeted Telegram tests và full suite nếu route graph thay đổi đáng kể.

## Phase 4: Tách Jira Selection Khỏi ChatReplyGraph

Mục đích: Jira issue workflow là workflow cấp turn, không còn nhánh trong chat graph.

- [ ] Di chuyển Jira workflow selection sang orchestrator.
- [ ] Xóa hoặc làm private-deprecated helper `_handle_jira_issue_prompt` trong chat graph.
- [ ] Giữ `JiraIssueAnalysisWorkflow` độc lập với Telegram.
- [ ] Giữ semantics `NIKO_JIRA_TOOLS_ENABLED`.
- [ ] Giữ semantics `NIKO_JIRA_DECISION_GATE_ENABLED`.
- [ ] Đảm bảo handoff Deep có Jira context vẫn ghi trace/runtime log.
- [ ] Chạy Jira workflow/tools tests.
- [ ] Chạy Telegram regression cho prompt Jira.

## Phase 5: Tách Memory Correction Selection

Mục đích: memory correction trở thành workflow cấp turn do orchestrator chọn.

- [ ] Di chuyển call `MemoryRuntime.handle_memory_correction(...)` ra orchestrator.
- [ ] Pending fact-ID follow-up vẫn qua durable pending facade hiện tại.
- [ ] Correction loop default-off vẫn fallback về V1 khi lỗi.
- [ ] `ChatReplyGraph` không còn chứa nhánh correction handled.
- [ ] Giữ trace `memory_correction_decision`, `memory_correction_clarify`,
      `memory_correction_applied`, `memory_correction_pending_*`.
- [ ] Chạy memory correction loop/store tests.
- [ ] Live-test lại delete/update/pending nếu thay đổi ảnh hưởng facade.

## Phase 6: Dashboard, Docs Và Cleanup

Mục đích: quan sát và tài liệu phản ánh kiến trúc mới, không còn mô tả
`ChatReplyGraph` như application core.

- [ ] Cập nhật dashboard graph semantics nếu UI có sơ đồ flow.
- [ ] Runtime log hiển thị workflow/orchestrator route khi cần.
- [ ] Cập nhật `AGENTS.md`.
- [ ] Cập nhật `README.md`.
- [ ] Cập nhật `docs/harness/architecture.md`.
- [ ] Cập nhật `docs/harness/telegram-chat-flow.md`.
- [ ] Cập nhật `docs/loop/architecture.md`.
- [ ] Cập nhật checklist này sau từng commit/phase.
- [ ] Dọn compatibility helper cũ khi không còn caller.

## Verification Tổng

Mục đích: khóa từng phase bằng test và kiểm docs/source không lệch nhau.

- [ ] Sau mỗi phase: chạy `rtk proxy git diff --check`.
- [ ] Phase gateway/chat: chạy `tests/test_telegram_prompt.py`.
- [ ] Phase Jira: chạy `tests/test_jira_issue_workflow.py` và `tests/test_jira_tools.py`.
- [ ] Phase memory correction: chạy memory correction/store/eval tests liên quan.
- [ ] Phase dashboard: chạy `tests/test_ops_dashboard.py`.
- [x] Trước khi push phase lớn: chạy `rtk proxy python -m pytest -q`.
- [ ] Rà `rtk rg` cho các cụm stale về `ChatReplyGraph`, `GatewayRunner`,
      `TurnOrchestrator`, `NikoApp`, `tool router`.
