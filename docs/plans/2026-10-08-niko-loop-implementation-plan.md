# Kế Hoạch Triển Khai Niko Loop Tổng Quát

Ngày tạo: 2026-10-08
Trạng thái: Phase 2 core V0, Phase 3 memory fact tools V0, Phase 4A correction loop default-off, Phase 4B durable pending correction và Phase 5 dashboard observability V0 đã triển khai, đã live-test

## Mục Tiêu

Tạo một Loop runtime tổng quát cho Niko theo hướng `observe -> reason -> act ->
observe`, đủ để dùng cho memory correction trước, rồi mở sang Jira/business tool
sau. Loop phải giữ đúng boundary hiện tại: Telegram gateway mỏng,
`ChatReplyGraph` điều phối route lớn, memory/Jira tool tự quản dữ liệu của mình,
trace/dashboard quan sát được từng bước.

## Nguyên Tắc Thiết Kế

- Graph bọc Loop, không thay thế Loop.
- Tool không tự gửi reply Telegram; tool chỉ trả kết quả cho Loop.
- Python runtime luôn validate trước khi mutate SQLite hoặc dữ liệu business.
- V0 ưu tiên Python-controlled loop vì Niko hiện gọi Claude qua `fcc-claude` CLI,
  chưa có native tool-use API trong application code.
- Interface phải đủ tổng quát để sau này thay controller bằng native LLM tool-use
  mà không phải viết lại ToolRegistry/observer.

## Phase 1: Documentation And Interface Contract

Mục đích: chốt ngôn ngữ kiến trúc và điểm nối trước khi viết code.

Việc cần làm:

- Tạo `docs/loop/architecture.md` mô tả vai trò Loop, ranh giới, interface mục tiêu
  và guardrail.
- Cập nhật README/architecture/memory flow/AGENTS để không ai hiểu nhầm Loop đã là
  runtime hoàn chỉnh.
- Tạo checklist theo phase để bám tiến độ.

Tiêu chí hoàn thành:

- Có tài liệu riêng cho Loop.
- Các docs hiện có dẫn tới tài liệu Loop.
- Tài liệu phân biệt rõ current state và target state.

## Phase 2: Loop Core V0

Mục đích: có runtime loop tối thiểu, độc lập Telegram và chưa gắn workflow cụ thể.

Việc cần làm:

- Tạo package `niko/loop/`.
- Định nghĩa `Tool`, `ToolContext`, `ToolResult`, `LoopResult`.
- Tạo `ToolRegistry` với register/schema/execute.
- Tạo `LoopRuntime` chạy theo max iteration và observer.
- Tạo observer ghi trace/runtime log nhưng không phụ thuộc dashboard frontend.

Tiêu chí hoàn thành:

- Unit test chứng minh Loop gọi read-only tool, nhận result, rồi final. Done V0.
- Unit test chứng minh tool lỗi không crash loop. Done V0.
- Unit test chứng minh max iteration dừng đúng và trả fallback. Done V0.

## Phase 3: Memory Tool Adapters

Mục đích: biến các thao tác fact hiện tại thành tool có contract rõ.

Việc cần làm:

- Tạo memory tools dựa trên `MemoryStore`/`MemoryRuntime`: `search_facts`,
  `list_facts`, `update_fact`, `delete_fact`.
- Giữ episode read-only trong V0.
- Đảm bảo mutate tool validate ID, replacement/action và ghi trace.
- Không xóa ngay `MemoryCorrectionWorkflow`; ban đầu chỉ thêm adapter để test.

Tiêu chí hoàn thành:

- Tool search/list trả đúng fact ID/subject/content. Done V0.
- Tool update/delete không mutate khi input thiếu hoặc ID không tồn tại. Done V0.
- LoopResult ghi `mutates_state` cho update/delete tool calls. Done V0.
- Trace/runtime log chi tiết khi nối chat flow vẫn là Phase 5.

## Phase 4: Correction Flow Uses Loop

Mục đích: thay dần pending correction V1 bằng Loop nhưng vẫn giữ hành vi user đã
test ổn.

Việc cần làm:

- Thêm route trong `ChatReplyGraph` hoặc `MemoryRuntime` để correction prompt có
  thể vào Loop memory workflow.
- Reuse precheck hiện tại để neutral prompt không bị kéo sai context.
- Chuyển ambiguous fact flow sang durable pending state thay vì state chỉ sống trong process.
- Giữ fallback về `MemoryCorrectionWorkflow` trong giai đoạn đầu nếu Loop lỗi.

Trạng thái 2026-10-08:

- Đã thêm `niko/memory/correction_loop.py` để chạy prompt sửa/xóa fact trực tiếp
  qua `LoopRuntime` + `search_facts` / `update_fact` / `delete_fact`.
- Đã thêm config `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=0`; đường mới chỉ chạy khi
  bật cùng `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=1`.
- Đã giữ fallback về V1 khi loop lỗi.
- Đã thêm durable pending state trong SQLite cho ambiguous fact-ID follow-up. Nhánh
  resolve vẫn đi qua facade V1 để giữ hành vi đã live-test, nhưng dữ liệu chờ chọn
  fact sống qua restart và có TTL 15 phút.
- Đã thêm trace events `memory_correction_pending_created`,
  `memory_correction_pending_resolved`, `memory_correction_pending_expired`.

Tiêu chí hoàn thành:

- Test xóa fact mơ hồ: bot liệt kê candidate và chỉ xóa khi user chọn ID hợp lệ.
- Test sửa fact: bot yêu cầu replacement nếu thiếu, update khi đủ.
- Test readonly inventory: câu hỏi xem fact không bị hiểu thành correction.

## Phase 5: Dashboard And Observability

Mục đích: nhìn được Loop trên dashboard giống runtime log hiện tại.

Việc cần làm:

- Thêm trace/runtime events cho Loop vào docs và dashboard reader nếu cần.
- Hiển thị tool calls theo turn trong trace/detail view hoặc runtime log.
- Giữ terminal bootstrap-only; operational log vẫn đi qua runtime log.

Tiêu chí hoàn thành:

- Một turn dùng Loop có thể xem được từng step/tool/result.
- Lỗi controller/tool hiển thị rõ nguồn lỗi.
- Không cần đọc terminal để biết Loop đã quyết định gì.

Trạng thái 2026-10-08:

- Trace view có khối `Loop Steps` theo turn, tóm tắt decision/tool/result trước khi
  xem raw JSONL.
- Runtime log nhận source `loop` với message ngắn cho từng event quan trọng như
  `loop_decision` và `loop_tool_call_finished`.
- `/api/traces` vẫn giữ raw event để các kiểm thử/dashboard đọc chung.

## Phase 6: Jira/Business Tool Lane

Mục đích: dùng cùng Loop runtime cho business data, tách khỏi chat memory local.

Việc cần làm:

- Thiết kế tool đọc Jira/mock Jira/public fixture.
- Normalize issue/comment/changelog thành context có evidence.
- Deep agent phân tích dựa trên context, không để tool tự kết luận.

Tiêu chí hoàn thành:

- Prompt có issue key kích hoạt tool fetch phù hợp.
- Trace ghi rõ tool nào fetch dữ liệu nào.
- Chat memory SQLite không bị dùng làm nơi lưu mặc định Jira/business records.

## Test Chung

- `python -m pytest`
- Test deterministic cho `niko/loop/` core.
- Test memory correction regression từ `docs/harness/memory-eval-scenarios.md`.
- Live test Telegram cho delete/update/list fact sau khi Phase 4 bật.
- Dashboard verification cho trace/runtime log sau Phase 5.

## Quyết Định Mặc Định

- V0 dùng Python-controlled loop.
- Max iteration mặc định: `3` cho memory correction, `5` cho Jira fetch flow.
- Mutating tools fail-closed.
- Read-only retrieval tools có thể fail-open bằng fallback text nếu an toàn.
- Episode vẫn read-only trong memory correction V0.
