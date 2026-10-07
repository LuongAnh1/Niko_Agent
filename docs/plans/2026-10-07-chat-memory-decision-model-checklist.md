# Checklist Chat Memory Decision Model

Ngày lập: 2026-10-07
Tài liệu gốc: [Kế Hoạch Chat Memory Decision Model](2026-10-07-chat-memory-decision-model.md)
Phạm vi: chat memory single-user trong `Niko_Agent`
Checklist kiểm tra live: [Memory Live Verification](../harness/memory-live-verification.md)

Checklist này bám theo các phase trong kế hoạch gốc. Mỗi nhóm có mục đích để khi
đọc lại mình biết checkbox đó phục vụ phần nào của memory pipeline, không chỉ là
một danh sách việc rời rạc.

## 0. Scope Và Ranh Giới

Mục đích: chốt rõ mình đang làm chat memory local cho Niko, không trộn với
lakehouse/Jira memory backend và không mở rộng multi-user quá sớm.

- [x] Tách lane chat memory khỏi lakehouse/Jira memory backend trong docs.
- [x] Chốt v1 là single-user chat memory.
- [x] Ghi rõ `facts/episodes` là memory chung của một Niko instance ở v1.
- [x] Ghi rõ `chat_log` vẫn giữ `session_id/conversation_id` để xem lịch sử hội thoại.
- [x] Ghi rõ lakehouse/Jira không nằm trong chat memory mặc định.
- [x] Ghi rõ V2 mới tính scoped memory: `global | user | conversation | project`.

## 0.5. Khung Memory Runtime

Mục đích: đưa memory về một pipeline trung tâm giống tinh thần Waku, để runtime/graph
không phải biết chi tiết gate, inventory, search và format context.

- [x] Thêm `niko/memory/runtime.py`.
- [x] Thêm `MemoryRuntime.retrieve_for_deep(...)`.
- [x] Thêm `MemoryRuntime.build_context_for_deep(...)`.
- [x] Thêm `default_memory_runtime()`.
- [x] Chuyển `runtime.call_deep_agent(...)` sang gọi `MemoryRuntime`.
- [x] Giữ `retrieve_memory_context(...)` và `build_memory_context(...)` làm wrapper tương thích.
- [x] Không đổi schema SQLite trong bước refactor khung.
- [x] Không thêm write gate/consolidation trong bước refactor khung.

## 1. Phase 1: Memory Decision Layer

Mục đích: tạo lớp quyết định nhẹ bằng local Ollama/Nimble để Niko hỏi các câu
hẹp về memory, thay vì để Deep agent tự quyết định mọi thứ.

- [x] Thêm `bots/decision_model/memory.py`.
- [x] Dùng lại `systemone_choice(...)` thay vì tạo client Ollama riêng.
- [x] Thêm `MemoryRetrievalDecision` cho kết quả retrieval gate.
- [x] Thêm `decide_memory_retrieval(...)`.
- [x] Thêm label hợp lệ `skip` và `retrieve`.
- [x] Thêm normalize alias cho `skip/retrieve`.
- [x] Thêm state tối thiểu gồm prompt và gateway metadata nhẹ.
- [x] Thêm criteria đóng để model chỉ chọn label.
- [x] Mở rộng `ChoiceDecision.extra` để nhận `query/reason`.
- [x] Export `MemoryRetrievalDecision` và `decide_memory_retrieval` từ `bots.decision_model`.
- [x] Thiết kế tiếp `decide_memory_write(...)`.
- [x] Thiết kế tiếp `classify_memory_candidate(...)`.
- [ ] Thiết kế tiếp `decide_memory_correction_intent(...)`.
- [x] Trong dashboard Config, mỗi cấu hình mới của Decision/Memory layer phải có
  chú thích rõ dùng để làm gì, ảnh hưởng runtime nào, và khi nào nên bật/tắt.

## 2. Phase 2: Retrieval Gate Cho Deep

Mục đích: giảm retrieval thừa, tránh kéo memory không liên quan vào Deep prompt,
nhưng vẫn fail-open để không bỏ lỡ memory thật sự cần.

- [x] Thêm config `NIKO_MEMORY_GATE_ENABLED`, default `0`.
- [x] Thêm field config trong dashboard tab `Memory & Trace`.
- [x] Gate default-off để không đổi hành vi demo hiện tại.
- [x] Nối gate vào `retrieve_memory_context(...)`.
- [x] Gate `skip` trả memory rỗng và không gọi search store.
- [x] Gate `retrieve` dùng query do model trả.
- [x] Gate `retrieve` fallback raw prompt nếu query rỗng.
- [x] Gate lỗi thì fail-open bằng raw prompt.
- [x] Inventory question như “đang lưu fact nào” bypass gate.
- [x] Thêm metadata gate vào trace `memory_retrieval`.
- [x] Thêm runtime log `memory_gate_decision`.
- [x] Thêm runtime log `memory_gate_error`.
- [x] Tránh circular import bằng import runtime logger cục bộ trong hàm log.
- [ ] Test thực tế trên Telegram với `NIKO_MEMORY_GATE_ENABLED=1`.
- [ ] Quan sát tab Bots xem log gate có đủ dễ đọc không.
- [ ] Quan sát tab Traces xem `gate_decision`, `gate_query`, `gate_reason` có đủ debug không.
- [x] Ghi prompt mẫu cho gate `skip`, `retrieve`, fail-open.
- [ ] Nếu Nimble hay thiếu `query`, chỉnh instructions của gate.
- [ ] Nếu gate làm chậm Deep rõ rệt, cân nhắc timeout riêng.

## 3. Phase 3: Unicode/Query Search Hardening

Mục đích: làm search facts/episodes ổn hơn với tiếng Việt, dấu câu và query lạ,
để retrieval gate có query tốt thì store cũng tìm đúng.

- [x] Có test baseline cho tiếng Việt có dấu trong `tests/test_memory_store.py`.
- [x] Có stopword logic tránh query generic kéo fact sai.
- [x] Rà lại `_fts_query` trong `niko/memory/store.py` theo hướng Unicode token.
- [x] Đảm bảo query rỗng không trả memory bừa.
- [x] Thêm test query toàn punctuation.
- [x] Thêm test query tiếng Việt có dấu và không dấu.
- [x] Thêm test chống prefix quá rộng kiểu `car` match `carpet` nếu không chủ ý.
- [x] Đảm bảo fallback LIKE vẫn cho kết quả hợp lý khi SQLite không có FTS5.

## 4. Phase 4: Write Gate Và Chat Consolidation

Mục đích: biến chat thật thành semantic facts/episodic events có kiểm soát,
không spam memory bằng small talk hoặc dữ liệu không bền vững.

- [x] Thiết kế `memory_write_gate` với label `discard` và `remember`.
- [x] Thêm config `NIKO_MEMORY_WRITE_GATE_ENABLED`.
- [x] Thêm trace/runtime log `memory_write_decision`.
- [x] Thêm trace/runtime log `memory_write_gate_error`.
- [x] Write gate v1 chỉ chặn `episodes`, không chặn operational `chat_log`.
- [x] Write gate lỗi thì fail-open và vẫn ghi episode baseline.
- [x] Thêm cột `consolidated INTEGER DEFAULT 0` cho `chat_log`.
- [x] Thêm migration idempotent cho DB cũ.
- [x] Thêm module `niko/memory/consolidation.py`.
- [x] Lấy batch chat log chưa consolidated theo batch size cố định.
- [x] Thêm API mark-done chỉ đánh dấu đúng row đã đọc.
- [x] Nối scaffold consolidation qua `MemoryRuntime` để giữ một cổng memory thống nhất.
- [ ] Thêm threshold tự động theo ngưỡng N exchange trước khi gọi model.
- [x] Tạo memory candidates bảo thủ từ batch bằng rule nội bộ.
- [ ] Summarizer tạo memory candidates từ batch.
- [x] Thêm `memory_type_classifier` để lọc `semantic_fact`, `episodic_event`, `discard`.
- [x] Ghi facts với source/provenance `consolidation`.
- [x] Ghi episodes với source/provenance `consolidation`.
- [x] Chỉ mark đúng rows đã đọc là consolidated.
- [x] Model lỗi hoặc output không parse được thì không mark consolidated.
- [x] No facts hợp lệ vẫn có thể mark done nếu batch chỉ là small talk.
- [x] Thêm dashboard/manual trigger nếu cần debug consolidation.

## 5. Phase 5: Memory Correction Qua Chat/Dashboard

Mục đích: cho người dùng sửa hoặc quên memory một cách rõ ràng, có kiểm soát,
thay vì để fact sai nằm mãi trong SQLite.

- [ ] Thiết kế `memory_correction_intent` với label `none`, `correct_memory`, `forget_memory`.
- [ ] Thêm config `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED`.
- [ ] Thêm trace/runtime log `memory_correction_decision`.
- [ ] Khi user nói “quên/sửa memory”, search facts/episodes liên quan trước.
- [ ] Nếu match chắc chắn, update/delete có trace.
- [ ] Nếu mơ hồ, hỏi lại user hoặc route Deep giải thích.
- [ ] Dashboard hỗ trợ update fact rõ ràng hơn nếu chưa đủ.
- [ ] Dashboard hỗ trợ delete episode nếu cần.
- [ ] Không để Deep tự sửa memory tự do ở phase này.

## 6. Phase 6: Working Memory Rõ Ràng Hơn

Mục đích: tách ba lớp context trong prompt Deep để tránh lẫn prompt hiện tại,
recent conversation và long-term memory.

- [ ] Thiết kế recent chat window theo `conversation_id`.
- [ ] Thêm budget cho recent conversation.
- [ ] Thêm budget cho long-term memory context.
- [ ] Format prompt Deep thành các section rõ: `Identity`, `Recent conversation`, `Relevant semantic facts`, `Relevant episodic events`, `Current user message`.
- [ ] Đảm bảo current user message luôn được ưu tiên nếu mâu thuẫn với memory.
- [ ] Đảm bảo long-term facts/episodes vẫn là memory chung của instance trong v1.
- [ ] Thêm trace metadata cho số recent turns được inject.

## 7. Phase 7: Eval Cho Chat Memory

Mục đích: chứng minh memory hoạt động bằng test/eval, không chỉ nhìn dashboard
và cảm giác “có vẻ chạy”.

- [x] Unit test decision model criteria/alias cho memory retrieval.
- [x] Unit test gate `skip` không gọi store search.
- [x] Unit test gate `retrieve` dùng query gate trả.
- [x] Unit test gate lỗi fail-open.
- [x] Unit test inventory question bypass gate.
- [x] Unit test trace có gate metadata.
- [ ] Eval prompt mẫu cho câu không cần memory.
- [ ] Eval prompt mẫu cho câu hỏi cần memory trực tiếp.
- [ ] Eval prompt mẫu cho câu hỏi cần memory gián tiếp.
- [ ] Eval prompt mẫu cho correction/forget khi phase đó có.
- [ ] Eval regression cho query tiếng Việt có dấu.
- [ ] Eval failure mode khi Ollama/Nimble không chạy.

## Acceptance Check Chung

Mục đích: mỗi lần sửa memory pipeline đều có checklist kỹ thuật tối thiểu trước
khi coi là xong.

- [x] `rtk python -m pytest` pass sau retrieval gate v1.
- [x] `rtk git diff --check` pass sau retrieval gate v1.
- [x] `rtk python -m pytest` pass sau search hardening Phase 3.
- [x] `rtk python -m pytest` pass sau refactor `MemoryRuntime`.
- [x] `rtk python -m pytest` pass sau write gate v1.
- [x] Link docs nội bộ pass.
- [ ] Bật gate trên dashboard không làm Telegram bot crash.
- [x] Config dashboard hiển thị mô tả dễ hiểu cho từng key memory/decision mới.
- [ ] Khi gate skip, Deep prompt không chứa `Semantic memory / facts`.
- [ ] Khi gate retrieve, Deep prompt có memory context phù hợp.
- [ ] Khi Ollama/Nimble lỗi, Deep vẫn chạy với retrieval fallback.

## Chưa Làm Cố Ý Ở V1

Mục đích: giữ scope nhỏ để memory pipeline chạy chắc trước khi mở rộng thành
multi-user hoặc business memory backend.

- [ ] Không thêm multi-user scoped memory.
- [ ] Không thêm `owner/scope` vào facts/episodes.
- [ ] Không nối mặc định sang lakehouse/Jira.
- [ ] Không để Deep tự ghi/sửa memory tự do.
- [ ] Không làm graph schema cho memory trong repo Niko.
