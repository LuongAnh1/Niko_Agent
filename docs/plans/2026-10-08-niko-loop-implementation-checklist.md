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

- [x] Tạo package `niko/loop/`.
- [x] Định nghĩa `Tool`, `ToolContext`, `ToolResult`, `LoopDecision`, `LoopResult`.
- [x] Tạo `ToolRegistry` với register/schema/execute.
- [x] Tạo `LoopRuntime` có max iteration và final fallback.
- [x] Tạo observer ghi trace/runtime log.
- [x] Viết unit test cho read-only tool, tool error và max iteration.

Tiêu chí hoàn thành:

- Loop core chạy được bằng test mà không cần Telegram.
- Tool lỗi không crash loop.
- Max iteration dừng đúng và có final result.

## Phase 3: Memory Tool Adapters

Mục đích: chuyển thao tác fact thành tool rõ contract để correction workflow
không còn phải ôm search/update/delete rải rác.

- [x] Tạo `search_facts`.
- [x] Tạo `list_facts`.
- [x] Tạo `update_fact`.
- [x] Tạo `delete_fact`.
- [x] Giữ episode read-only trong V0.
- [x] Gắn `mutates_state=True` cho update/delete để LoopResult/observer phân biệt mutate tool.
- [x] Test input thiếu, ID không tồn tại, ID hợp lệ.

Tiêu chí hoàn thành:

- Tool trả kết quả đủ để controller/Deep trả lời user.
- Mutate tool không ghi DB khi input chưa đủ chắc.

Trạng thái 2026-10-08:

- Đã thêm `niko/memory/loop_tools.py` với `build_memory_fact_tools(...)`.
- Đã thêm `tests/test_memory_loop_tools.py` cho search/list/update/delete và LoopRuntime integration nhẹ.
- Chưa nối các tools này vào `MemoryCorrectionWorkflow` hoặc `ChatReplyGraph`.

## Phase 4: Correction Flow Uses Loop

Mục đích: đưa luồng sửa/xóa fact đang tạm trong Phase 5 V1 sang Loop nhưng không
làm mất hành vi đã live-test ổn.

- [x] Route correction prompt trực tiếp vào Loop memory workflow khi bật
      `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=1`.
- [x] Giữ precheck để neutral prompt không inherit correction context cũ.
- [x] Ambiguous fact follow-up dùng durable pending state thay vì state chỉ sống trong process.
- [x] Có fallback về `MemoryCorrectionWorkflow` khi Loop lỗi trong giai đoạn đầu.
- [x] Thêm config dashboard cho `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=0`.
- [x] Unit test delete fact rõ target qua Loop.
- [x] Unit test update fact rõ target qua Loop.
- [x] Unit test thiếu replacement và ambiguous target không mutate DB.
- [x] Unit test loop lỗi fallback về V1.
- [x] Live test delete ambiguous fact.
- [x] Unit test delete no-match không được hỏi chọn ID từ weak matches.
- [x] Live retest delete no-match sau relevance filter.
- [x] Live test update unique có replacement.
- [x] Live test update fact thiếu replacement.
- [x] Live test readonly inventory không bị coi là correction.

Tiêu chí hoàn thành:

- Bot vẫn hỏi lại khi target mơ hồ.
- Bot chỉ update/delete khi ID/action/replacement hợp lệ.
- Trace đọc được toàn bộ quyết định và tool call.

Trạng thái 2026-10-08:

- Đã xong Phase 4A default-off. Direct correction prompt có thể chạy qua
  `MemoryCorrectionLoopWorkflow`, dùng LoopRuntime và fact tools, rồi trả kết
  quả về facade V1.
- Live test unique delete phát hiện Nimble có thể trả intent `forget_memory`
  nhưng bỏ trống `query`, làm search theo cả prompt và match nhiều fact
  `checklist`. Đã thêm fallback trích query từ prompt sửa/xóa và ranking bảo thủ
  trong Loop: query đủ cụ thể như `anh thích checklist màu xanh` chọn fact vượt
  trội; query ngắn như `checklist` vẫn hỏi lại.
- Live test delete ambiguous thật đã pass: query ngắn `checklist` làm bot hỏi lại
  danh sách fact, follow-up `fact #...` được pending V1 xóa đúng ID.
- Live test delete no-match phát hiện search trả weak matches chỉ vì chung từ
  chung như `thích`; bot đã hỏi chọn ID dù prompt `anh thích bánh màu cầu vồng`
  không có fact thật. Đã thêm relevance filter trong Loop: candidate chỉ được
  giữ nếu khớp đủ từ nội dung đặc trưng; match yếu bị coi là `no_fact_match`.
  Đã thêm unit regression; live retest sau sửa đã pass với
  `memory_correction_clarify` reason `no_fact_match`.
- Live test update unique có replacement đã pass: prompt `sửa fact anh thích
  checklist màu đỏ thành anh thích checklist màu tím` đi qua `search_facts`,
  chọn fact #12, gọi `update_fact`, ghi `memory_correction_applied` và SQLite
  lưu `previous_content` trong meta.
- Live test update thiếu replacement đã pass: prompt `sửa fact checklist màu tím`
  đi qua `search_facts`, tìm fact #12 nhưng không gọi `update_fact`; Loop trả
  clarify `missing_replacement` và không ghi `memory_correction_applied`.
- Live test readonly inventory đã pass: prompt `hiện tại em có những fact gì về
  anh` bị correction precheck skip với reason `no_explicit_correction_signal`,
  sau đó retrieval gate dùng `fact_mode=list`; không có `update_fact` hoặc
  `delete_fact`.
- Phase 4B đã thêm bảng `memory_correction_pending`: ambiguous follow-up lưu
  `conversation_id`, action, query, replacement, candidate fact IDs, trace ID và TTL
  15 phút. Reply chỉ chọn `fact #...` có thể resolve sau khi tạo `MemoryRuntime`
  mới; ID ngoài danh sách không mutate DB; pending hết hạn bị clear và có trace
  `memory_correction_pending_expired`.
- Unit test Phase 4B đã thêm cho durable create/resolve, invalid ID, expired
  pending và ambiguous update có replacement. Targeted run:
  `rtk python -m pytest tests/test_memory_correction_loop.py tests/test_memory_store.py -q`
  -> `67 passed`.
- Live test durable pending qua restart đã pass trên Telegram thật: bot hỏi chọn
  `fact_ids=[12, 6, 7]`, dashboard stop/start bot giữa hai lượt, follow-up
  `fact #12 nhé` được resolve bằng pending SQLite, ghi
  `memory_correction_context_fallback`, `memory_correction_applied action=delete_fact fact_id=12`
  và `memory_correction_pending_resolved`.
- Live test invalid ID đã pass: follow-up `fact #199 nhé` với pending `[6, 7]`
  ghi `memory_correction_clarify` reason `pending_fact_id_not_offered`, không có
  `memory_correction_applied`.
- Live test ambiguous update có replacement đã pass: prompt sửa `checklist` tạo
  pending `[6, 7]`, follow-up `fact #6 nhé` ghi `update_fact fact_id=6`,
  `memory_correction_pending_resolved`; SQLite xác nhận fact #6 đổi content, fact #7
  giữ nguyên và pending được clear.
- Live test expired pending đã pass: pending `[13, 7]` được chỉnh `expires_at` về
  quá khứ, restart Telegram Bot để bỏ RAM cache, follow-up `fact #13 nhé` ghi
  `memory_correction_pending_expired`, không có `memory_correction_applied`;
  SQLite xác nhận fact #13 và #7 vẫn còn, pending được clear.
- Phase 4B live đã khóa các nhánh chính: durable resolve qua restart, invalid ID,
  ambiguous update có replacement, và expired pending.

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
