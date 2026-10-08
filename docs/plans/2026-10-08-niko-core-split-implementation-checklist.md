# Checklist Triển Khai Tách Core, Gateway Và Graph Cho Niko

Ngày lập: 2026-10-08
Tài liệu gốc: `docs/plans/2026-10-08-niko-core-split-implementation-plan.md`
Trạng thái: Phase 6 đã hoàn tất dashboard/docs/runtime-log cleanup cho core split

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

- [x] Tạo `NikoApp` hoặc `create_niko_app()`.
- [x] App quản lý MemoryStore/MemoryRuntime/TraceLogger/ChatReplyGraph.
- [x] App quản lý dependency hiện dùng; không giữ slot Jira workflow nếu slot đó chưa được gọi.
- [x] Gateway runner nhận app instance.
- [x] Dashboard/bot startup dùng app factory thống nhất.
- [x] Tests vẫn inject được store/logger tạm.
- [x] Chạy Telegram + ops dashboard tests.

Kết quả kiểm thử Phase 2, 2026-10-08:

- `rtk proxy python -m pytest tests/test_niko_app.py tests/test_gateway_runner.py tests/test_telegram_prompt.py tests/test_ops_dashboard.py -q`
  pass `65 passed`.
- `rtk proxy python -m pytest -q` pass `216 passed`.
- `rtk proxy git diff --check` pass, chỉ có warning CRLF do cấu hình Git/Windows.

## Phase 3: Review Boundary Theo Waku

Mục đích: đảm bảo boundary mới không chỉ là cầu nối hình thức. Waku để app điều
phối một turn và để workflow thật nằm dưới graph, nên Niko không giữ package
orchestrator nếu nó chỉ forward sang `ChatReplyGraph`.

- [x] So sánh lại với Waku: `waku/app.py` là turn entrypoint, `waku/graph/` chứa workflow thật.
- [x] Xóa package `niko/orchestration/` vì `TurnOrchestrator` V0 chỉ delegate.
- [x] Xóa test riêng cho orchestrator mỏng.
- [x] Gỡ injection `orchestrator` và `jira_workflow` chưa dùng trong `NikoApp`.
- [x] App giữ thứ tự xử lý hiện tại bằng cách forward trực tiếp sang `ChatReplyGraph`.
- [x] Busy/followup behavior vẫn không tạo duplicate Deep job.
- [x] Normal chat fallback vẫn đi qua `ChatReplyGraph`.
- [x] Trace `turn_start`/`turn_end` vẫn nhất quán.
- [x] Chạy targeted Telegram tests và full suite nếu route graph thay đổi đáng kể.

Kết quả kiểm thử Phase 3 trước khi review lại, 2026-10-08:

- `rtk proxy python -m pytest tests/test_turn_orchestrator.py tests/test_niko_app.py tests/test_gateway_runner.py tests/test_telegram_prompt.py tests/test_ops_dashboard.py -q`
  pass `68 passed`.
- `rtk proxy python -m pytest -q` pass `219 passed`.
- `rtk proxy git diff --check` pass, chỉ có warning CRLF do cấu hình Git/Windows.

Ghi chú review 2026-10-08:

- Folder `niko/orchestration/` bị coi là bridge chưa cần thiết vì khác vocabulary
  của Waku mà chưa sở hữu workflow selection thật.
- Luồng active quay về `GatewayRunner -> NikoApp -> ChatReplyGraph`.
- Cần chạy lại test sau khi dọn bridge.

## Phase 4: Tách Jira Selection Khỏi ChatReplyGraph

Mục đích: Jira issue workflow là workflow cấp turn, không còn nhánh trong chat graph.

- [x] Di chuyển Jira workflow selection sang app-level workflow selection trong `NikoApp`.
- [x] Xóa helper `_handle_jira_issue_prompt` trong chat graph.
- [x] Giữ `JiraIssueAnalysisWorkflow` độc lập với Telegram.
- [x] Giữ semantics `NIKO_JIRA_TOOLS_ENABLED`.
- [x] Giữ semantics `NIKO_JIRA_DECISION_GATE_ENABLED`.
- [x] Đảm bảo handoff Deep có Jira context vẫn ghi trace/runtime log.
- [x] Di chuyển call-site memory correction lên `NikoApp` để giữ thứ tự route/busy -> correction -> Jira -> normal chat.
- [x] Chạy Jira workflow/tools tests.
- [x] Chạy Telegram regression cho prompt Jira.

Kết quả kiểm thử Phase 4, 2026-10-08:

- `rtk proxy python -m pytest tests/test_niko_app.py tests/test_gateway_runner.py tests/test_telegram_prompt.py tests/test_jira_issue_workflow.py tests/test_jira_tools.py tests/test_memory_correction_loop.py -q`
  pass `88 passed`.

## Phase 5: Tách Memory Correction Selection

Mục đích: memory correction trở thành workflow cấp turn do app-level workflow
selection chọn.

- [x] Di chuyển call `MemoryRuntime.handle_memory_correction(...)` ra lớp chọn workflow cấp turn.
- [x] Pending fact-ID follow-up vẫn qua durable pending facade hiện tại.
- [x] Correction loop default-off vẫn fallback về V1 khi lỗi.
- [x] `ChatReplyGraph` không còn chứa nhánh correction handled.
- [x] Giữ trace `memory_correction_decision`, `memory_correction_clarify`,
      `memory_correction_applied`, `memory_correction_pending_*`.
- [x] Chạy memory correction loop/store tests.
- [x] Live-test lại delete/update/pending nếu thay đổi ảnh hưởng facade.

Kết quả kiểm thử Phase 5, 2026-10-08:

- Thêm regression app-level: busy route bỏ qua correction; correction handled kết
  thúc turn trước Jira/normal chat; correction unhandled mới đi tiếp normal chat.
- `rtk proxy powershell -NoProfile -Command "python -m pytest tests/test_niko_app.py tests/test_memory_correction_loop.py tests/test_memory_store.py tests/test_memory_eval_scenarios.py -q"`
  pass `83 passed`.
- `rtk proxy powershell -NoProfile -Command "python -m pytest tests/test_telegram_prompt.py tests/test_gateway_runner.py -q"`
  pass `52 passed`.
- Không cần live-test mới vì không đổi facade runtime correction; thay đổi chỉ khóa
  thứ tự xử lý ở app bằng regression tests.

## Phase 6: Dashboard, Docs Và Cleanup

Mục đích: quan sát và tài liệu phản ánh kiến trúc mới, không còn mô tả
`ChatReplyGraph` như application core.

- [x] Cập nhật dashboard graph semantics nếu UI có sơ đồ flow.
- [x] Runtime log hiển thị workflow route khi cần.
- [x] Cập nhật `AGENTS.md`.
- [x] Cập nhật `README.md`.
- [x] Cập nhật `docs/harness/architecture.md`.
- [x] Cập nhật `docs/harness/telegram-chat-flow.md`.
- [x] Cập nhật `docs/loop/architecture.md`.
- [x] Cập nhật checklist này sau từng commit/phase.
- [x] Rà compatibility helper cũ; giữ lại helper còn caller và ghi rõ lý do.

Kết quả triển khai Phase 6, 2026-10-08:

- Dashboard live graph đổi sang `Gateway -> GatewayRunner -> NikoApp -> Workflow`;
  test template khóa label `GatewayRunner`, `NikoApp`, `workflow_selected`.
- `NikoApp` ghi runtime log source `niko_app`, event `workflow_selected`, với
  `workflow`, `route`, `conversation_id`, `trace_id` và metadata Jira khi có.
- `ChatReplyGraph.handle_message(...)` và `finish_workflow_reply(...)` được giữ có
  chủ ý: entrypoint tương thích cho caller cũ và helper ghi reply/trace/log chung.
- `rtk proxy powershell -NoProfile -Command "python -m pytest tests/test_niko_app.py tests/test_gateway_runner.py tests/test_telegram_prompt.py tests/test_ops_dashboard.py -q"`
  pass `71 passed`.

## Verification Tổng

Mục đích: khóa từng phase bằng test và kiểm docs/source không lệch nhau.

- [x] Sau mỗi phase: chạy `rtk proxy git diff --check`.
- [x] Phase gateway/chat: chạy `tests/test_telegram_prompt.py`.
- [x] Phase Jira: chạy `tests/test_jira_issue_workflow.py` và `tests/test_jira_tools.py`.
- [x] Phase memory correction: chạy memory correction/store/eval tests liên quan.
- [x] Phase dashboard: chạy `tests/test_ops_dashboard.py`.
- [x] Trước khi push phase lớn: chạy `rtk proxy python -m pytest -q`.
- [x] Rà `rtk rg` cho các cụm stale về `ChatReplyGraph`, `GatewayRunner`,
      `TurnOrchestrator`, `NikoApp`, `tool router`.

Kết quả rà stale 2026-10-08:

- Không còn cụm stale kiểu correction detection cũ hoặc trạng thái Phase 4 chờ
  verification. Các kết quả còn lại là ghi chú lịch sử/target boundary có chủ ý.

Kết quả rà stale Phase 6, 2026-10-08:

- Rà các cụm route cũ và mô tả gọi thẳng vào chat graph; kết quả còn lại chỉ là
  assertion trong test dashboard để chống hồi quy.
- `rtk proxy powershell -NoProfile -Command "git diff --check"` pass, chỉ có
  warning CRLF do Git/Windows.
- `rtk proxy powershell -NoProfile -Command "python -m pytest -q"` pass `222 passed`.
