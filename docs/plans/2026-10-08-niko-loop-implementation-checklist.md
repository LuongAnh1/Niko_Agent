# Checklist Triển Khai Niko Loop Tổng Quát

Ngày tạo: 2026-10-08
Tài liệu gốc: `docs/plans/2026-10-08-niko-loop-implementation-plan.md`

## Phase 1: Documentation And Interface Contract

Mục đích: chốt cách hiểu chung về Loop trước khi đụng code, để không trộn nhầm
Loop với Telegram gateway, MemoryRuntime hoặc ChatReplyGraph hiện tại.

- [x] Tạo `docs/loop/architecture.md`.
- [x] Ghi rõ Loop hiện là target architecture, chưa phải runtime hoàn chỉnh.
- [x] Cập nhật README để trỏ tới tài liệu Loop.
- [x] Cập nhật `docs/harness/architecture.md` với boundary của Loop.
- [x] Cập nhật `docs/memory/chat-memory-architecture-flow.md` để correction V1
      được đặt trong hướng chuyển sang Loop/tool workflow.
- [x] Cập nhật `AGENTS.md` để session sau giữ đúng boundary.

Tiêu chí hoàn thành:

- Người đọc biết Loop nằm ở đâu, dùng để làm gì, và chưa nên coi là code đã có.
- Checklist/plan có đủ phase sau để implement tiếp.

## Phase 2: Loop Core V0

Mục đích: tạo runtime loop tối thiểu, độc lập với Telegram và workflow cụ thể.

- [ ] Tạo package `niko/loop/`.
- [ ] Định nghĩa `Tool`, `ToolContext`, `ToolResult`, `LoopResult`.
- [ ] Tạo `ToolRegistry` với register/schema/execute.
- [ ] Tạo `LoopRuntime` có max iteration và final fallback.
- [ ] Tạo observer ghi trace/runtime log.
- [ ] Viết unit test cho read-only tool, tool error và max iteration.

Tiêu chí hoàn thành:

- Loop core chạy được bằng test mà không cần Telegram.
- Tool lỗi không crash loop.
- Max iteration dừng đúng và có final result.

## Phase 3: Memory Tool Adapters

Mục đích: chuyển thao tác fact thành tool rõ contract để correction workflow
không còn phải ôm search/update/delete rải rác.

- [ ] Tạo `search_facts`.
- [ ] Tạo `list_facts`.
- [ ] Tạo `update_fact`.
- [ ] Tạo `delete_fact`.
- [ ] Giữ episode read-only trong V0.
- [ ] Ghi trace/runtime log cho tool mutate.
- [ ] Test input thiếu, ID không tồn tại, ID hợp lệ.

Tiêu chí hoàn thành:

- Tool trả kết quả đủ để controller/Deep trả lời user.
- Mutate tool không ghi DB khi input chưa đủ chắc.

## Phase 4: Correction Flow Uses Loop

Mục đích: đưa luồng sửa/xóa fact đang tạm trong Phase 5 V1 sang Loop nhưng không
làm mất hành vi đã live-test ổn.

- [ ] Route correction prompt vào Loop memory workflow.
- [ ] Giữ precheck để neutral prompt không inherit correction context cũ.
- [ ] Ambiguous fact flow dùng Loop result/state thay vì pending RAM thuần.
- [ ] Có fallback về `MemoryCorrectionWorkflow` khi Loop lỗi trong giai đoạn đầu.
- [ ] Live test delete ambiguous fact.
- [ ] Live test update fact thiếu replacement.
- [ ] Live test readonly inventory không bị coi là correction.

Tiêu chí hoàn thành:

- Bot vẫn hỏi lại khi target mơ hồ.
- Bot chỉ update/delete khi ID/action/replacement hợp lệ.
- Trace đọc được toàn bộ quyết định và tool call.

## Phase 5: Dashboard And Observability

Mục đích: debug Loop trên dashboard thay vì đọc terminal hoặc đoán từ output bot.

- [ ] Chuẩn hóa event `loop_started`, `loop_decision`, `loop_tool_call_started`,
      `loop_tool_call_finished`, `loop_final_answer`, `loop_error`.
- [ ] Dashboard/trace view đọc được tool call/result theo turn.
- [ ] Runtime log hiển thị source/event/message cho Loop.
- [ ] Docs hướng dẫn cách xem Loop decision khi live test.

Tiêu chí hoàn thành:

- Một turn dùng Loop có bảng event đủ rõ.
- Lỗi controller/tool nhìn được nguyên nhân và step lỗi.

## Phase 6: Jira/Business Tool Lane

Mục đích: chứng minh Loop là khung tổng quát, không chỉ phục vụ chat memory.

- [ ] Thiết kế tool lane Jira/mock Jira.
- [ ] Parse issue key từ prompt.
- [ ] Fetch issue/comment/changelog từ source demo.
- [ ] Normalize context có evidence cho Deep.
- [ ] Test prompt hỏi issue key route qua Loop/tool.

Tiêu chí hoàn thành:

- Deep trả lời dựa trên dữ liệu issue đã fetch.
- Chat memory SQLite không bị trộn thành nơi lưu Jira/business records.

