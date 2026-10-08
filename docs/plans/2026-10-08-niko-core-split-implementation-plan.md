# Kế Hoạch Triển Khai Tách Core, Gateway Và Graph Cho Niko

Ngày lập: 2026-10-08
Trạng thái: Phase 1 Gateway Runner khung đã triển khai, đang chờ khóa bằng test
Tài liệu nền: `docs/plans/2026-10-08-niko-core-split-survey.md`

## Mục Đích

Tài liệu này chuyển kết quả khảo sát core/gateway/graph thành phương hướng triển
khai cụ thể. Mục tiêu không phải viết lại Niko thành framework lớn ngay, mà là
tách dần các trách nhiệm đang dồn vào `ChatReplyGraph` để sau này Telegram, Jira
bot, CLI hoặc gateway khác có thể cắm vào cùng một core xử lý turn.

Nguyên tắc chính:

- Không đổi hành vi user-facing ở các phase đầu.
- Bọc đường gọi hiện tại trước, di chuyển policy sau.
- Mỗi phase phải có test regression rõ ràng trước khi sang phase tiếp.
- Không trộn Jira bot, Jira runtime tools và lakehouse/KG vào cùng một lớp.
- Không biến `niko/loop/` thành application core; Loop chỉ là tool workflow runtime.

## Target Architecture

Kiến trúc đích sau refactor:

```text
bots/<gateway>/
  -> niko.chat_gateway.ChatGatewayMessage
  -> niko.gateway.GatewayRunner
  -> niko.app.NikoApp
  -> niko.orchestration.TurnOrchestrator
     -> memory correction workflow
     -> Jira issue workflow
     -> chat reply workflow
        -> Fast/Deep/runtime/memory context
```

Vai trò từng lớp:

- `bots/<gateway>/`: platform IO, auth, parsing, reply/sticker/webhook. Không chứa
  policy chọn agent, memory hoặc business workflow.
- `niko/gateway/`: runner chung cho gateway. Nhận message đã chuẩn hóa, gọi app,
  serialize turn khi cần, bắt lỗi cấp gateway và trả kết quả cho callback reply.
- `niko/app.py` hoặc `niko/agent_app.py`: assembly root. Ráp MemoryStore,
  MemoryRuntime, TraceLogger, ChatReplyGraph, JiraIssueAnalysisWorkflow và các
  dependency runtime.
- `niko/orchestration/`: chọn workflow cấp turn. Lớp này quyết định prompt hiện tại
  đi memory correction, Jira issue hay normal chat.
- `niko/graphs/`: workflow nghiệp vụ cụ thể. Chat graph không cần biết Jira slot
  hoặc correction slot sau khi tách xong.
- `niko/loop/`: Tool/ToolRegistry/LoopRuntime/observer generic.
- `niko/tools/`: adapter domain tool như memory facts, Jira fixture/API, future ops.

## Phase 1: Gateway Runner Khung

Mục đích: tạo lớp runner chung nhưng vẫn giữ `ChatReplyGraph` là điểm xử lý thật.

Trạng thái 2026-10-08: đã thêm `niko/gateway/GatewayRunner` và chuyển Telegram
gateway sang gọi runner. Phase này vẫn giữ nguyên behavior, route labels, trace
events và runtime logs vì runner chỉ forward sang `ChatReplyGraph`.

Thay đổi chính:

- Thêm package `niko/gateway/` với runner nhận `ChatGatewayMessage`,
  `deliver_reply`, `notify_working`.
- Runner gọi `ChatReplyGraph.handle_message(...)` bên trong, chưa đổi route nào.
- Telegram bot dùng runner thay vì gọi global graph trực tiếp.
- Giữ tên route, trace event và runtime log như hiện tại.

Tiêu chí hoàn thành:

- Telegram message vẫn trả lời như trước.
- Unit tests Telegram hiện có pass không cần sửa nhiều expectation.
- Không có workflow nào bị chuyển khỏi `ChatReplyGraph` ở phase này.

## Phase 2: NikoApp Assembly Root

Mục đích: gom wiring vào một chỗ thay vì để gateway hoặc module global tự ráp graph.

Thay đổi chính:

- Thêm `NikoApp` hoặc `create_niko_app()`.
- App sở hữu hoặc inject các dependency chính: memory store/runtime, trace logger,
  chat workflow, Jira workflow và config-dependent runtime helpers.
- Gateway runner nhận app instance và gọi một method xử lý turn thống nhất.
- Giữ compatibility path cho tests hoặc caller cũ trong thời gian chuyển tiếp.

Tiêu chí hoàn thành:

- Telegram bot không tự tạo graph/runtime trực tiếp.
- Tests vẫn có thể inject MemoryStore/TraceLogger như hiện tại.
- Dashboard start/stop bot không đổi UX.

## Phase 3: TurnOrchestrator V0

Mục đích: đưa lựa chọn workflow cấp turn ra khỏi chat workflow nhưng chưa tháo hết
logic cùng lúc.

Thay đổi chính:

- Thêm `TurnOrchestrator` trong `niko/orchestration/` hoặc package tương đương.
- Orchestrator nhận prompt, gateway message, reply callbacks và app dependencies.
- Orchestrator chạy thứ tự hiện tại: busy/followup guard, memory correction, Jira
  issue workflow nếu bật, rồi fallback normal chat.
- Trong V0 có thể gọi helper cũ để giữ rủi ro thấp, nhưng public boundary phải
  là orchestrator.

Tiêu chí hoàn thành:

- Trace vẫn có `turn_start`, `route_decision`, `turn_end` như trước.
- Busy Deep job và followup không bị tạo duplicate job.
- Local/Fast/Deep route vẫn giữ cùng route label.

## Phase 4: Tách Jira Selection Khỏi ChatReplyGraph

Mục đích: Jira không còn là nhánh nằm trong chat workflow.

Thay đổi chính:

- Di chuyển logic chọn/chạy `JiraIssueAnalysisWorkflow` sang orchestrator.
- `ChatReplyGraph` không còn `_handle_jira_issue_prompt`.
- Jira workflow vẫn trả `JiraIssueAnalysisResult` và orchestrator quyết định reply
  ngay hoặc handoff Deep với context.
- `NIKO_JIRA_TOOLS_ENABLED` và `NIKO_JIRA_DECISION_GATE_ENABLED` giữ semantics cũ.

Tiêu chí hoàn thành:

- Prompt có issue key vẫn fetch fixture và handoff Deep.
- Prompt issue không có fixture vẫn trả no-data reply và không gọi Deep.
- Prompt Jira mơ hồ vẫn dùng Decision Gate khi bật.

## Phase 5: Tách Memory Correction Selection

Mục đích: memory correction là workflow cấp turn, không còn là chi tiết của chat
reply workflow.

Thay đổi chính:

- Orchestrator gọi `MemoryRuntime.handle_memory_correction(...)` trước normal chat.
- Pending fact-ID follow-up vẫn đi qua facade hiện tại để giữ durable pending.
- Correction loop default-off vẫn giữ fallback về V1.
- `ChatReplyGraph` không cần biết correction handled/reply nữa.

Tiêu chí hoàn thành:

- Delete/update ambiguous, invalid ID, expired pending và readonly inventory vẫn
  pass các kịch bản đã live-test.
- Không prompt neutral nào bị kéo nhầm vào correction context.
- Trace `memory_correction_*` giữ đọc được trên dashboard.

## Phase 6: Dashboard, Docs Và Cleanup

Mục đích: làm observability và tài liệu khớp kiến trúc mới sau khi code đã tách.

Thay đổi chính:

- Dashboard graph semantics đổi thành
  `Gateway -> GatewayRunner -> TurnOrchestrator -> Workflow`.
- Runtime log nên cho biết workflow nào xử lý turn.
- Cập nhật `AGENTS.md`, `README.md`, `docs/harness/architecture.md`,
  `docs/harness/telegram-chat-flow.md`, `docs/loop/architecture.md`.
- Dọn compatibility helper cũ chỉ khi tests và docs đã trỏ sang lớp mới.

Tiêu chí hoàn thành:

- Người đọc docs không còn hiểu `ChatReplyGraph` là application core.
- Dashboard vẫn debug được route, loop step, Jira gate và memory correction.
- Full suite pass trước khi push.

## Verification Chung

Sau mỗi phase refactor code:

- Chạy targeted tests cho vùng vừa đổi.
- Chạy `rtk proxy git diff --check`.
- Chạy full `rtk proxy python -m pytest -q` trước commit/push phase lớn.
- Cập nhật checklist triển khai ngay trong cùng commit hoặc commit docs riêng liền kề.

Các test nhóm chính:

- Telegram/gateway/chat behavior: `tests/test_telegram_prompt.py`.
- Jira workflow/tools: `tests/test_jira_issue_workflow.py`, `tests/test_jira_tools.py`.
- Memory correction: `tests/test_memory_correction_loop.py`, memory store/eval tests
  liên quan.
- Loop/runtime/dashboard observability: `tests/test_loop_runtime.py`,
  `tests/test_ops_dashboard.py`.

## Rủi Ro Cần Kiểm Soát

- Race với Deep job đang chạy khi di chuyển busy/followup ra orchestrator.
- Trace event đổi tên làm dashboard hoặc live debug khó đọc.
- Gateway runner vô tình nuốt lỗi khiến Telegram không trả error reply.
- App assembly tạo singleton sai làm tests chia sẻ state.
- Jira/memory workflow bị gọi trước local reply theo cách làm thay đổi UX hiện tại.

## Trạng Thái Hiện Tại

Phase 1 đã bắt đầu bằng một lớp `GatewayRunner` mỏng để bọc đường gọi hiện tại.
Chưa có `NikoApp`, chưa có `TurnOrchestrator`, và chưa di chuyển Jira/memory
selection ra khỏi `ChatReplyGraph`.
