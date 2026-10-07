# Checklist Live Test Chat Memory Phase 6/7

Ngày lập: 2026-10-07
Phạm vi: kiểm tra live luồng chat memory sau khi đã siết `correction gate` và tách
working memory cho Deep khỏi context quyết định sửa/xóa memory.

Tài liệu liên quan:

- [Luồng kiến trúc chat memory](../memory/chat-memory-architecture-flow.md)
- [Checklist phase tổng](2026-10-07-chat-memory-decision-model-checklist.md)
- [Nhật ký live verification](../harness/memory-live-verification.md)

## 1. Chuẩn Bị Runtime Sạch

Mục đích: đảm bảo Telegram Bot đang chạy đúng code mới, không bị process cũ giữ lock
hoặc chạy logic correction gate cũ.

- [ ] Dashboard đang chạy trước bot: `rtk python -m niko.ops.dashboard`.
- [ ] Decision Model đã warmup thành công trong tab `Bots`.
- [ ] Telegram Bot được start từ dashboard, trạng thái không phải `external` stale.
- [ ] Sau khi sửa code, bot đã được stop/start lại trước khi test live.
- [ ] Runtime log có event start mới của Telegram Bot sau thời điểm sửa code gần nhất.

## 2. Config Bắt Buộc Cho Đợt Test Này

Mục đích: khóa đúng các gate cần quan sát, tránh nhầm pass/fail do một gate đang tắt.

- [ ] `NIKO_MEMORY_ENABLED=1`.
- [ ] `NIKO_MEMORY_RETRIEVAL_ENABLED=1`.
- [ ] `NIKO_MEMORY_GATE_ENABLED=1`.
- [ ] `NIKO_MEMORY_WRITE_ENABLED=1`.
- [ ] `NIKO_MEMORY_WRITE_GATE_ENABLED=1`.
- [ ] `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=1`.
- [ ] Dashboard Config có chú thích rõ từng key memory/decision dùng để làm gì.

## 3. Correction Precheck Không Bắt Nhầm Prompt Trung Tính

Mục đích: xác nhận câu mới không có ý định sửa/xóa memory sẽ không bị recent correction
history kéo sang nhánh `correct_memory` hoặc `forget_memory`.

Prompt đã dùng:

```text
Trong bài test phase 6 này, từ khóa tạm thời là quả mận xanh
```

Kỳ vọng:

- [x] Có `memory_correction_skipped`.
- [x] Runtime log `memory_correction_decision` có `model=python_precheck`.
- [x] `reason=no_explicit_correction_signal`.
- [x] `recent_turn_count=0` trong correction decision context.
- [x] Không có `memory_correction_clarify` cho turn này.
- [x] Không có `memory_correction_applied` cho turn này.

Kết quả đã quan sát:

- [x] Pass lúc 2026-10-07 16:36 UTC sau khi restart Telegram Bot: precheck bỏ qua
  correction gate đúng, rồi turn đi tiếp qua route thường.

## 4. Route Thường Vẫn Chạy Sau Correction Skip

Mục đích: đảm bảo precheck chỉ chặn correction gate, không làm hỏng fast/deep routing.

Kỳ vọng cho prompt trung tính ở nhóm 3:

- [x] Có `route_decision` trước correction precheck.
- [x] Route cuối là `fast_agent` hoặc nhánh hợp lệ khác tùy triage.
- [x] Nếu vào fast route, trace có `fast_triage_started` và `fast_triage_finished`.
- [x] Nếu Decision Model triage chọn `reply_now`, bot vẫn trả lời bình thường.

Kết quả đã quan sát:

- [x] Pass lúc 2026-10-07 16:36 UTC: turn kết thúc `route=fast_agent`,
  `fast_triage_finished` dùng Ollama/Nimble và bot trả lời đúng.

## 5. Deep Working Memory Dùng Recent Conversation Đúng Chỗ

Mục đích: kiểm tra working memory ngắn hạn thuộc Deep prompt, không thuộc mặc định của
correction gate. Gate retrieval có thể `skip` long-term facts/episodes, nhưng recent
conversation vẫn được phép xuất hiện trong Deep context.

Prompt đề xuất để ép Deep thay vì fast reply:

```text
Phân tích ngắn giúp anh: trong test phase 6 vừa rồi, từ khóa tạm thời anh đưa là gì, và vì sao câu đó không nên lưu thành fact dài hạn?
```

Kỳ vọng:

- [x] Correction precheck vẫn `memory_correction_skipped` vì prompt không yêu cầu sửa/xóa fact.
- [x] Turn đi `deep_agent` hoặc `fast_agent -> deep_agent` tùy route.
- [x] Nếu Deep được gọi, trace có `memory_retrieval`.
- [x] `memory_retrieval` có `recent_turn_count > 0`.
- [x] Nếu retrieval gate chọn `skip`, Deep prompt vẫn có recent conversation nhưng không có long-term facts/episodes.
- [x] Bot nhắc đúng "quả mận xanh" dựa trên working memory gần đây.

Kết quả đã quan sát:

- [x] Pass lúc 2026-10-07 16:57 UTC: route `deep_agent`, correction precheck
  `python_precheck`, `memory_retrieval` có `recent_turn_count=6`, long-term
  retrieval `skip`, `fact_count=0`, `episode_count=0`; bot nhắc đúng từ khóa
  "quả mận xanh" và write gate chọn `discard`.

## 6. Inventory Long-Term Memory Vẫn Qua Retrieval Decision Model

Mục đích: xác nhận câu hỏi "đang lưu fact nào" là retrieval/inventory, không bị correction
gate bắt nhầm và không quay lại heuristic search hard-code.

Prompt:

```text
Hiện tại em đang lưu những fact nào về anh?
```

Kỳ vọng:

- [x] Correction precheck bỏ qua hoặc correction decision là `none`.
- [x] Có `memory_gate_decision`.
- [x] `decision=retrieve`.
- [x] `fact_mode=list`.
- [x] `episode_mode=none`.
- [x] Trace `memory_retrieval` có `fact_count` đúng với snapshot memory hiện tại.
- [x] Write gate chọn `discard`; không tạo `memory_write_episode` cho lượt inspect.

Kết quả đã quan sát:

- [x] Pass lúc 2026-10-07 17:01 UTC: route `deep_agent`, correction precheck
  `python_precheck`, retrieval gate `decision=retrieve`, `label=list_facts`,
  `fact_mode=list`, `episode_mode=none`; trace `memory_retrieval` có
  `fact_count=2`, `episode_count=0`, và write gate chọn `discard`.
- [x] Retest sau khi xóa fact tạm lúc 2026-10-07 17:14 UTC: route `deep_agent`,
  retrieval gate vẫn `decision=retrieve`, `label=list_facts`, `fact_mode=list`,
  `episode_mode=none`; trace có `fact_ids=[7, 6]`, `fact_count=2`,
  `episode_count=0`, `recent_turn_count=6`. Bot liệt kê đúng facts #6/#7,
  đồng thời nhắc fact #9 đã bị xóa dựa trên recent working memory, và write gate
  tiếp tục `discard`.

## 7. Correction Explicit Vẫn Hoạt Động

Mục đích: đảm bảo việc siết precheck không làm hỏng lệnh sửa/xóa memory thật sự.

Prompt mẫu:

```text
Niko, quên fact về checklist giúp anh
```

Kỳ vọng:

- [x] Lệnh xóa mơ hồ hỏi lại thay vì xóa bừa.
- [x] Follow-up kiểu `fact #8 nhé` dùng pending action trong Python, validate ID rồi mới xóa.
- [x] Trace có `memory_correction_context_fallback` khi xử lý pending fact ID.
- [x] Trace có `memory_correction_applied` khi delete thành công.
- [x] Fact test tạm được xóa đúng target và không còn trong SQLite sau khi chọn ID.
- [ ] Test sửa fact end-to-end sau khi chọn ID được để dành cho Loop/tool workflow,
  trừ khi anh muốn ép kiểm thử V1 sâu hơn.

Kết quả đã quan sát:

- [x] Pass lúc 2026-10-07 17:07 UTC: prompt xóa fact màu tím vào correction flow,
  Decision Model chọn `forget_memory`; do match nhiều fact nên bot hỏi lại với
  `fact_ids=[9, 6, 7]`. Follow-up `fact #9 nha` đi qua
  `memory_correction_context_fallback`, runtime dùng pending action
  `forget_memory`, apply `delete_fact fact_id=9`; scan SQLite sau đó không còn
  fact nào chứa nội dung màu tím.

## 8. Ghi Nhận Sau Mỗi Test

Mục đích: tránh phải đọc lại toàn bộ trace/runtime log sau này.

- [x] Preflight tự động trước live test tiếp: `git diff --check` pass, targeted pytest pass
  `78 passed`, full pytest pass `144 passed`.
- [ ] Sau mỗi prompt live, ghi pass/fail vào
  [Memory Live Verification](../harness/memory-live-verification.md).
- [ ] Nếu có chỉnh code, ghi rõ lỗi quan sát được, file đã sửa và unit test đã chạy.
- [ ] Nếu chỉ chỉnh docs/checklist, chạy `rtk git diff --check`.
- [ ] Nếu chỉnh logic memory, chạy targeted tests trước rồi mới chạy suite rộng hơn.
