# Memory Live Verification

Ngày lập: 2026-10-07
Phạm vi: kiểm tra live chat memory trên Telegram qua Ops dashboard

Tài liệu này dùng sau khi unit test đã pass. Mục tiêu là kiểm tra các gate memory
trong môi trường chạy thật: dashboard, Ollama/Nimble, Telegram Bot, trace và
runtime log.

## Chuẩn Bị

1. Chạy dashboard:

```bash
rtk python -m niko.ops.dashboard
```

2. Mở `http://127.0.0.1:7777`.
3. Trong tab `Bots`, bấm `Warmup` cho Decision Model.
4. Trong tab `Config`, bật từng key theo từng ca kiểm tra:
   - `NIKO_MEMORY_RETRIEVAL_ENABLED=1`
   - `NIKO_MEMORY_GATE_ENABLED=1`
   - `NIKO_MEMORY_WRITE_ENABLED=1`
   - `NIKO_MEMORY_WRITE_GATE_ENABLED=1`
   - `NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED=1` nếu đang test auto consolidation.
   - `NIKO_MEMORY_CONSOLIDATE_EVERY_N_EXCHANGES=1` hoặc `2` để test nhanh, sau đó trả về ngưỡng demo.
5. Trong tab `Bots`, start Telegram Bot từ dashboard.

Nếu test đang tập trung vào retrieval gate, có thể giữ write gate tắt để log dễ
đọc hơn. Nếu test write gate, nên giữ retrieval gate bật để xem hai gate cùng
hiện trong log.

## Prompt Mẫu

| Mục tiêu | Prompt Telegram | Kỳ vọng chính |
| --- | --- | --- |
| Retrieval `skip` | `Em giải thích nhanh decorator trong Python là gì?` | `memory_gate_decision` có `decision=skip`; Deep prompt không cần memory context. |
| Retrieval `retrieve` | `Anh đã bảo em nhớ sở thích làm docs của anh là gì nhỉ?` | `memory_gate_decision` có `decision=retrieve`; trace `memory_retrieval` có `gate_query`. |
| Inventory qua Decision Model | `Hiện tại em đang lưu những fact nào về anh?` | `memory_gate_decision` có `decision=retrieve`, label `list_facts` hoặc `fact_mode=list`, `episode_mode=none`; facts được list thay vì search bằng chữ `fact`. |
| Write `discard` | `oke cảm ơn em` | `memory_write_decision` có `decision=discard`; không tạo episode mới. |
| Write inventory discard | `Hiện tại em đang lưu những fact nào về anh?` | Sau khi Niko list memory, `memory_write_decision` nên là `discard`; không có `memory_write_episode` mới cho chính lượt inspect/list memory. |
| Write `remember` | `Lên kế hoạch sửa memory runtime để tuần sau anh demo với thầy.` | `memory_write_decision` có `decision=remember`; có `memory_write_episode`. |
| Consolidation fact | `Ghi nhớ rằng anh thích checklist có mục đích rõ ràng.` | Memory tab `Refresh batch` có candidate `semantic_fact`; `Run once` ghi fact source `consolidation`. |
| Consolidation discard | `haha oke` | Candidate `discard`; run once mark rows nhưng không ghi fact/episode. |
| Auto consolidation | Hai exchange ngắn, trong đó có một câu `Ghi nhớ rằng...` | Khi bật auto và đủ ngưỡng, trace/runtime log có `memory_consolidation_auto_started/finished`; fact/episode được ghi với source `consolidation`. |

## Quan Sát Trên Dashboard

Trong tab `Bots` hoặc bảng runtime log, tìm các event:

- `memory_gate_decision`: có `decision`, `query`, `reason`, `fact_mode`,
  `episode_mode`, `confidence`, `model`.
- `memory_gate_error`: Ollama/Nimble lỗi; retrieval phải fail-open bằng raw prompt.
- `memory_retrieval`: có facts/episodes được retrieve và metadata gate.
- `memory_write_decision`: có `remember` hoặc `discard`.
- `memory_write_gate_error`: write gate lỗi; episode baseline vẫn được ghi nếu memory write bật.
- `memory_write_episode`: có episode mới khi write gate cho phép.
- `consolidation`: xem kết quả trong Memory tab sau khi bấm `Run once`.
- `memory_consolidation_auto_started`: auto worker bắt đầu xử lý một batch đủ complete exchange.
- `memory_consolidation_auto_finished`: auto worker kết thúc, có `status`, `marked_count`, `facts_written`, `episodes_written`.
- `memory_consolidation_auto_skipped`: auto bật nhưng chưa đủ exchange hoặc worker đang chạy.
- `memory_consolidation_auto_error`: auto worker lỗi; batch không được mark nếu classifier lỗi.

Trong tab `Traces`, kiểm tra một turn Deep có đủ thứ tự tối thiểu:

```text
turn_start
route_decision
memory_retrieval
deep_agent_call_started
deep_agent_call_finished
memory_write_decision
memory_write_episode hoặc không có episode nếu discard
reply_delivered
turn_end
```

## Fallback Cần Đúng

- Nếu Ollama/Nimble tắt trong lúc retrieval gate bật, Deep vẫn chạy với retrieval
  fail-open và runtime log có `memory_gate_error`.
- Nếu write gate lỗi, `chat_log` vẫn phải được ghi. Lỗi gate không được làm mất
  operational log.
- Nếu consolidation classifier lỗi, batch không được mark `consolidated=1`; anh
  có thể chạy lại sau khi model ổn.
- Nếu auto consolidation bật nhưng chỉ có wait/busy reply, batch không được chạy;
  auto chỉ tính assistant reply thật như `local_reply`, `fast_agent`, `deep_agent_final`.
- Nếu config đến từ OS env, dashboard phải hiển thị field bị khóa và không ghi
  đè vào `config.json`.

## Nhật Ký Test Live 2026-10-07

Mục đích: ghi lại kết quả test thật để lần sau không phải suy lại từ trace/log.
Các trace/runtime log cụ thể nằm trong `niko/.runtime/`; không đưa token, chat id
hay nội dung riêng tư dài vào tài liệu này.

### Kết Quả Đã Xác Nhận

- Retrieval `skip`: pass. Prompt giải thích Python đi `deep_agent`, memory gate
  chọn `skip`, trace `memory_retrieval` không kéo fact/episode vào Deep prompt.
- Retrieval `retrieve`: pass. Prompt hỏi lại sở thích checklist đi `deep_agent`,
  memory gate chọn `retrieve`, trace lấy được fact liên quan.
- Inventory chung: lần đầu fail. Trace cũ có `gate_label=inventory_bypass`,
  nhưng `fact_count=0` vì inventory chung bị rơi sang `search_facts(...)` thay
  vì `list_facts(...)`.
- Inventory chung sau khi chỉnh: pass. Runtime log có `label=list_facts`,
  `decision=retrieve`, `fact_mode=list`, `episode_mode=none`; trace
  `memory_retrieval` ghi `fact_count=2`, `episode_count=0`, nghĩa là runtime đã
  list facts thay vì search theo chữ `fact`.
- Regression 2026-10-07 14:49 UTC: dashboard `/api/memory` vẫn trả facts bình
  thường, nhưng Telegram turn "hiện tại em có những fact nào về anh" bị gate trả
  `label=retrieve`, `fact_mode=search`, `episode_mode=search`, nên runtime đi
  nhầm `search_facts(...)` và không list fact inventory. Đã thêm guardrail hẹp ở
  retrieval layer: khi model đã quyết định `decision=retrieve` cho prompt
  inventory rõ ràng, execution mode được ép về `fact_mode=list`,
  `episode_mode=none`, `query=""`.
- Write gate sau inventory chung: đã tinh chỉnh instructions/state và thêm unit
  regression sau lần live đầu bị nhiễu. Lượt inventory pass nhưng
  write gate chọn `remember` và ghi một episode mới cho chính câu inspect memory;
  đây không làm hỏng retrieval, nhưng về lâu dài nên hướng model `discard` các
  lượt chỉ liệt kê/kiểm tra memory để tránh nhiễu episodic memory. Cần chạy lại
  live prompt này để xác nhận Nimble đã chọn `discard`.
- Write gate `discard`: pass. Prompt test tạm thời đi `deep_agent`, write gate
  chọn `discard`, không có `memory_write_episode`.
- Write gate `remember`: pass. Prompt lập kế hoạch demo memory đi `deep_agent`,
  write gate chọn `remember`, trace có `memory_write_episode`.
- Consolidation backlog: dashboard/backend còn nhiều `chat_log` cũ chưa
  consolidated, nên `Refresh batch` ban đầu chưa tới câu test mới.
- Consolidation explicit fact: phát hiện candidate `semantic_fact` đúng ý, nhưng
  trước khi sửa code còn hai vấn đề: nội dung bị thừa chữ `rằng`, và batch explicit
  fact vẫn sinh thêm `episodic_event` trùng.
- Consolidation explicit fact sau khi sửa: pass. `Run once` ghi fact mới source
  `consolidation`, mark rows test là `consolidated=1`, và không ghi thêm
  consolidation episode trùng.
- Consolidation discard: pass. Prompt small talk tạo candidate `discard`, `Run once`
  mark rows test là `consolidated=1`, không tăng số fact/episode dài hạn.

### Điều Chỉnh Đã Làm Trong Lúc Test

- Đã backup SQLite runtime trước khi bỏ qua backlog cũ:
  `niko/.runtime/backups/niko_memory_before_skip_backlog_20261007-155259.sqlite3`.
- Đã mark các `chat_log` cũ trước row test là `consolidated=1` để `Refresh batch`
  nhảy tới batch mới. Đây là skip backlog runtime, không xóa vật lý dữ liệu.
- Không xóa episodic memory cũ. Hiện episodic cũ không nguy hiểm ngay vì memory
  gate/top-k hạn chế retrieval, nhưng hơi nhiễu nếu chứa câu hỏi low-signal. Việc
  dọn episodic nên làm bằng dashboard/tool riêng sau, không làm lẫn với test
  consolidation.
- Đã sửa consolidation để câu `Ghi nhớ rằng...` strip sạch tiền tố và batch có
  explicit fact không sinh thêm episodic candidate trùng.
- Đã thêm regression test cho explicit fact consolidation: content phải sạch tiền
  tố và candidate list chỉ còn `semantic_fact`.
- Đã bỏ nhánh inventory bypass bằng keyword Python. Retrieval gate giờ nhận thêm
  `list_facts`/`recent_episodes` và `fact_mode`/`episode_mode` từ Decision Model;
  inventory chung phải là `decision=retrieve`, `fact_mode=list`,
  `episode_mode=none`.
- Đã thêm regression test cho inventory model-driven: câu hỏi inventory chung
  phải đi qua retrieval decision rồi list facts, không quay lại stopword heuristic.
- Đã restart Telegram bot sau khi bỏ stale lock, chạy lại inventory prompt và xác
  nhận live log/trace đã theo mode `list_facts`.
- Đã bổ sung regression cho write gate inventory discard: instructions/state nhắc
  rõ lượt chỉ inspect/list memory nên `discard`, và runtime không ghi episode khi
  write gate trả `discard`. Cần live verify lại trên Telegram để xác nhận Nimble
  chọn đúng label trong điều kiện thật.
- Đã bổ sung regression cho case 14:49: Decision Model có thể trả
  `retrieve/search`, nhưng câu kiểm kê fact rõ ràng vẫn phải thực thi bằng
  `fact_mode=list`, `episode_mode=none`.
- Sau mỗi test live cần ghi lại kết quả vào tài liệu này ngay, gồm pass/fail,
  trace/log đáng chú ý và chỉnh sửa phát sinh.

### Ghi Chú UI

- Trong tài liệu cũ từng gọi là preview consolidation; trên dashboard hiện tại nút
  tương ứng là `Refresh batch`.
- `Refresh batch` chỉ đọc batch/candidate, không ghi memory.
- `Run once` mới gọi classifier, ghi fact/episode nếu được chọn, rồi mark đúng
  rows trong batch là consolidated.
- Auto consolidation được bật/tắt trong `Config -> Memory & Trace`; mặc định tắt
  để tránh xử lý backlog cũ bất ngờ khi demo.

## Khi Nào Coi Là Pass

- Dashboard Config hiển thị mô tả dễ hiểu cho các key memory/decision.
- Mỗi prompt mẫu tạo đúng event kỳ vọng trong Runtime Log hoặc Trace.
- Không có Telegram bot crash khi bật retrieval gate/write gate.
- Memory tab preview/run consolidation không ghi bừa khi prompt là small talk.
- Khi bật auto consolidation với ngưỡng thấp, trace/runtime log phải cho thấy auto
  chỉ chạy sau complete exchange và không tính wait/busy reply.
- Full test suite vẫn pass sau khi chỉnh config/docs.
## Kết quả cập nhật 2026-10-07

### Inventory memory không ghi episode mới

Mục đích: kiểm tra regression sau khi write gate được dạy rằng lượt hỏi inventory/list memory chỉ là thao tác đọc.

Kết quả đã quan sát trong trace/runtime log:

- `memory_gate_decision` chọn `retrieve`.
- `fact_mode=list`, `episode_mode=none`.
- `memory_write_decision` chọn `discard`.
- Không xuất hiện `memory_write_episode` cho lượt inventory sau khi restart/test lại.

Kết luận: test Phase 4 này đã pass. Các lượt hỏi "Niko đang lưu fact nào" hiện được xem là memory inspection, không còn tự sinh episode dài hạn.

### Phase 5 V1 live verification

Kết luận hiện tại: Phase 5 V1 đủ dùng như lớp tạm thời cho baseline chat memory. Luồng delete mơ hồ
và follow-up chọn ID đã pass live; luồng sửa fact đã phát hiện và vá lỗi thiếu `replacement`, có
unit test bảo vệ. Các test mở rộng còn lại nên để sang Loop/tool workflow thay vì làm Phase 5 phình
thêm.

- Unit test targeted đã được chạy thủ công với kết quả `42 passed`.
- Sau live test ambiguous, phát hiện lượt trả lời `fact #8` bị Decision Model phân loại `none` nên rơi sang Deep vì model chỉ thấy prompt hiện tại. Thiết kế hiện tại đưa vài lượt chat gần nhất vào Decision Model; pending metadata chỉ giữ danh sách ID hợp lệ và làm guardrail cuối.
- Cùng lúc bổ sung guardrail cho prompt có `quên/xóa`: nếu model lỡ chọn `correct_memory`, runtime sẽ ưu tiên `forget_memory` để đúng ý xóa.
- Unit test sau sửa: `tests/test_memory_store.py` pass `43 passed`; `tests/test_decision_model.py` pass `15 passed`.
- Sau khi review lại thiết kế, đã nâng cấp correction gate sang decision context: state gửi Nimble có `current_prompt` và `recent_turns` làm ngữ cảnh chính; `active_workflow`, `pending_action`, `pending_choices`, `pending_replacement` chỉ là metadata phụ cho workflow đang dang dở.
- Unit test sau decision context: `tests/test_memory_store.py` pass `44 passed`; `tests/test_decision_model.py` pass `15 passed`; `tests/test_telegram_prompt.py` pass `43 passed`.
- Live retest trên Telegram: lượt `fact #8 nhé` đã có `recent_turn_count=6`, `active_workflow=memory_correction`, `pending_action=forget_memory`, nhưng Nimble vẫn phân loại nhầm `correct_memory` và bot hỏi replacement.
- Đã thêm guardrail cho reply chỉ chọn fact ID trong pending workflow: nếu model mislabel action, runtime dùng `pending_action` cũ, validate ID trong `pending_choices`, và ghi `model_decision` vào trace.
- Unit test sau guardrail này: `tests/test_memory_store.py` pass `45 passed`.
- Live retest tiếp theo cho câu inventory `hiện tại em có những fact gì về anh` cho thấy correction gate bắt nhầm thành `correct_memory`.
- Đã soi riêng `bots/decision_model/memory/correction.py`, sửa chữ ký `choice_fn` sau khi tách package và thêm read-only guardrail: câu list/inspect memory ép về `none` để retrieval gate xử lý.
- Unit test sau read-only guardrail: `tests/test_decision_model.py` pass `16 passed`; bộ liên quan memory/decision/dashboard pass `69 passed`.
- Live test 2026-10-07 16:16 UTC cho câu Phase 6 `Trong bài test phase 6 này, từ khóa tạm thời là quả mận xanh.` cho thấy lỗi giao thoa Phase 5/6: route ban đầu là `fast_agent`, nhưng correction gate đọc 6 recent turns toàn nội dung sửa/xóa fact checklist và bắt nhầm thành `correct_memory` với `clarify_reason=missing_replacement`.
- Kết luận sau khi đối chiếu Waku: working memory/recent history nên là context được scope theo run hoặc workflow. Gate nguy hiểm như correction không nên tự động ăn toàn bộ recent history; prompt hiện tại phải là bằng chứng chính.
- Đã sửa correction workflow: prompt trung tính không có marker sửa/xóa/quên sẽ bypass Decision Model và ghi `memory_correction_skipped`; reply chỉ chọn pending fact ID dùng `pending_action` trong Python, không gọi model lần hai.
- Unit test targeted sau fix scope correction context: `tests/test_memory_store.py::MemoryCorrectionRuntimeTests` và test read-only trong `tests/test_decision_model.py` pass `11 passed`.
- Live retest 2026-10-07 16:36 UTC sau khi restart Telegram Bot: pass. Prompt trung tính Phase 6 ghi
  `memory_correction_skipped`, runtime log có `model=python_precheck`,
  `reason=no_explicit_correction_signal`, `recent_turn_count=0`, rồi route thường tiếp tục bằng
  `fast_agent`. Không có `memory_correction_clarify` hay `memory_correction_applied` cho turn này.
- Đã thêm checklist live riêng cho Phase 6/7 tại
  `docs/plans/2026-10-07-chat-memory-live-test-checklist.md` để các prompt tiếp theo có expected
  trace/log rõ ràng trước khi test.
- Automated preflight 2026-10-07 trước khi test live tiếp: `rtk proxy git diff --check` pass,
  targeted pytest cho correction/decision/eval/telegram routing pass `78 passed`, full suite pass
  `144 passed`.
- Live test Phase 6 working memory 2026-10-07 16:57 UTC: pass. Prompt hỏi lại từ khóa tạm thời
  đi `deep_agent`; correction precheck ghi `memory_correction_skipped` với
  `model=python_precheck`, `reason=no_explicit_correction_signal`, `recent_turn_count=0`.
  Deep retrieval ghi `gate_decision=skip`, `gate_fact_mode=none`, `gate_episode_mode=none`,
  `fact_count=0`, `episode_count=0`, nhưng vẫn có `recent_turn_count=6`. Bot nhắc đúng
  "quả mận xanh", và write gate chọn `discard` nên không tạo episode dài hạn cho lượt test.
- Live test inventory long-term memory 2026-10-07 17:01 UTC: pass. Prompt hỏi Niko đang lưu
  fact nào đi `deep_agent`; correction precheck ghi `memory_correction_skipped` với
  `model=python_precheck`. Retrieval gate chọn `decision=retrieve`, `label=list_facts`,
  `fact_mode=list`, `episode_mode=none`; trace `memory_retrieval` ghi `fact_count=2`,
  `episode_count=0`. Write gate chọn `discard`, nên lượt inspect memory không tạo episode mới.
- Live test correction delete fact tạm 2026-10-07 17:07 UTC: pass. Prompt xóa fact
  "checklist màu tím" vào correction flow; model chọn `forget_memory`. Runtime tìm nhiều
  fact khớp nên hỏi lại với `fact_ids=[9, 6, 7]`, không xóa ngay. Follow-up `fact #9 nha`
  dùng `memory_correction_context_fallback`, giữ pending action `forget_memory`, apply
  `delete_fact fact_id=9`; scan SQLite sau đó không còn fact nào chứa nội dung màu tím.
- Live retest inventory sau delete 2026-10-07 17:14 UTC: pass. Retrieval gate vẫn chọn
  `decision=retrieve`, `label=list_facts`, `fact_mode=list`, `episode_mode=none`;
  trace ghi `fact_ids=[7, 6]`, `fact_count=2`, `episode_count=0`, `recent_turn_count=6`.
  Bot liệt kê đúng hai fact còn lại và nhắc fact #9 đã xóa dựa trên recent working memory,
  không phải vì fact #9 còn trong long-term memory. Write gate tiếp tục `discard`.
- Bật `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=1` trong dashboard Config.
- Thêm một fact test rồi nhắn Telegram yêu cầu quên fact đó.
- Kiểm tra trace có `memory_correction_decision` và `memory_correction_applied`.
- Thêm hai fact cùng chủ đề rồi nhắn lệnh quên mơ hồ; Niko phải hỏi lại và không xóa gì.
- Live test 2026-10-07 15:11 UTC cho lệnh `Niko, quên fact về checklist giúp anh`: pass.
  Correction gate chọn `forget_memory`, confidence khoảng `0.829`, `recent_turn_count=6`;
  runtime trả `memory_correction_clarify` với `clarify_reason=ambiguous_fact_match` và
  `fact_ids=[8, 6, 7]`. Không có `memory_correction_applied` trong turn này, nên chưa xóa gì.
- Live follow-up 2026-10-07 15:14 UTC cho `fact #8 nhé`: pass.
  Correction context giữ `active_workflow=memory_correction`, `pending_action=forget_memory`
  và `pending_choices=[8, 6, 7]`; trace có `memory_correction_applied` với
  `action=delete_fact`, `fact_id=8`, reply xác nhận đã xóa fact #8. Snapshot memory sau đó
  chỉ còn fact #6 và #7 trong nhóm checklist.
- Live test sửa fact 2026-10-07 15:17 UTC: bot hỏi lại đúng vì câu sửa checklist khớp cả
  fact #7 và #6, chưa mutate dữ liệu. Log cũng cho thấy Nimble gốc chọn nhầm `forget_memory`
  nhưng Python guardrail đổi intent cuối sang `correct_memory`.
- Sau test sửa fact, đã vá `correction.py` để khi intent cuối là `correct_memory` nhưng model bỏ
  trống `replacement`, runtime trích phần sau `thành`/`thay bằng`. Unit test mới pass trong
  `tests/test_decision_model.py`.
- Deferred cho Loop/tool workflow: test sửa fact end-to-end sau khi chọn ID và retrieval lại nội dung
  mới; test xóa fact duy nhất không cần hỏi lại; dashboard delete/update episode nếu sau này cho phép.

## Kết quả cập nhật 2026-10-08

### Phase 4B Loop correction - durable pending qua restart

Mục đích: xác nhận ambiguous correction follow-up không còn phụ thuộc RAM của process bot. Sau khi bot hỏi chọn fact ID,
anh restart Telegram Bot rồi gửi `fact #...`; runtime phải đọc lại pending từ SQLite, validate ID, mutate đúng fact và clear
pending của lượt đó.

Kết quả live test: pass.

- Lượt đầu lúc 2026-10-08 03:23:10 UTC gửi `Niko, quên fact checklist`.
- Correction gate chọn `forget_memory`, `query=checklist`; Loop chạy `search_facts`, thấy nhiều fact khớp và trả clarify với `fact_ids=[12, 6, 7]`.
- Trace có `memory_correction_pending_created`, `pending_trace_id=60fb24aa-9a82-40af-90db-4f5f8bf346ee`, TTL 15 phút.
- Runtime log xác nhận dashboard stop/start bot giữa hai lượt: `telegram_bot_stop_finished` lúc 03:23:36 UTC, `telegram_bot_started` lúc 03:23:39 UTC.
- Lượt follow-up lúc 2026-10-08 03:23:57 UTC gửi `fact #12 nhé`.
- Trace có `memory_correction_context_fallback`, sau đó `memory_correction_applied action=delete_fact fact_id=12`.
- Trace có `memory_correction_pending_resolved` trỏ về `pending_trace_id=60fb24aa-9a82-40af-90db-4f5f8bf346ee`, xác nhận pending được nối lại sau restart.
- Kiểm tra SQLite sau test: fact #12 không còn trong bảng `facts`.
- Lưu ý sau test: conversation còn một pending mới được tạo ở lượt test sau lúc 03:25:48 UTC cho `fact_ids=[6, 7]`; đây không phải pending còn sót của Test 1.

### Phase 4B Loop correction - invalid ID và update ambiguous

Mục đích: xác nhận pending durable không mutate khi user chọn ID ngoài danh sách, sau đó vẫn xử lý được một workflow ambiguous update có replacement và clear pending khi resolve.

Kết quả live test: pass.

- Lượt Test 2 lúc 2026-10-08 03:24:56 UTC gửi `Niko, quên fact checklist`; Loop hỏi lại với `fact_ids=[6, 7]` và trace có `memory_correction_pending_created`.
- Follow-up lúc 2026-10-08 03:25:20 UTC gửi `fact #199 nhé`.
- Trace ghi `memory_correction_context_fallback`, sau đó `memory_correction_clarify` với `clarify_reason=pending_fact_id_not_offered`.
- Không có `memory_correction_applied` trong turn `fact #199 nhé`, nên DB không bị mutate khi ID không thuộc pending choices.
- Lượt Test 3 lúc 2026-10-08 03:25:43 UTC gửi `Niko, sửa fact checklist thành anh thích checklist có tiêu chí hoàn thành rõ ràng`.
- Guardrail đưa decision cuối về `correct_memory`; Loop tìm nhiều fact khớp và tạo pending mới với `fact_ids=[6, 7]`, `replacement=anh thích checklist có tiêu chí hoàn thành rõ ràng`.
- Follow-up lúc 2026-10-08 03:26:16 UTC gửi `fact #6 nhé`.
- Trace ghi `memory_correction_context_fallback`, `memory_correction_applied action=update_fact fact_id=6`, và `memory_correction_pending_resolved`.
- Kiểm tra SQLite sau test: fact #6 đã đổi content thành `anh thích checklist có tiêu chí hoàn thành rõ ràng`, fact #7 giữ nguyên, `memory_correction_pending` không còn row cho conversation này.

### Phase 4B Loop correction - expired pending không mutate

Mục đích: xác nhận pending đã hết hạn thì follow-up `fact #...` không được dùng lại action cũ để xóa/sửa fact.

Kết quả live test: pass.

- Lượt đầu lúc 2026-10-08 03:38:04 UTC gửi `Niko, quên fact checklist`.
- Loop hỏi lại với `fact_ids=[13, 7]`; trace có `memory_correction_pending_created`, `pending_trace_id=3cd0e7ce-b072-4f13-a90d-d841e67c61ff`.
- Để test nhanh, chỉnh `expires_at` của pending trong SQLite về `2000-01-01T00:00:00Z`, sau đó restart Telegram Bot để RAM cache không còn giữ hạn cũ.
- Follow-up lúc 2026-10-08 03:40:26 UTC gửi `fact #13 nhé`.
- Trace ghi `memory_correction_pending_expired`, `expires_at=946684800.0`, trỏ về đúng `pending_trace_id=3cd0e7ce-b072-4f13-a90d-d841e67c61ff`.
- Không có `memory_correction_applied` trong turn expired.
- Reply báo lựa chọn fact trước đó đã hết hạn và yêu cầu gửi lại yêu cầu sửa/xóa memory.
- Kiểm tra SQLite sau test: fact #13 và fact #7 vẫn còn, `memory_correction_pending` đã clear.

### Phase 5 Loop observability - checklist live sắp chạy

Mục đích: xác nhận dashboard đọc được từng step của Loop mà không cần quay lại terminal.
Phần này kiểm tra observability, không thay đổi lại hành vi correction đã pass ở Phase
4B.

Trước khi test:

- Dashboard đang chạy bản code mới.
- Telegram Bot đã restart từ tab `Bots` sau khi cập nhật dashboard/template.
- `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=1`.
- `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=1`.
- Decision Model đã warmup.

Prompt smoke test đề xuất:

- Ambiguous delete: `Niko, quên fact checklist`.
- Update có replacement: `Niko, sửa fact checklist thành anh thích checklist có mục tiêu rõ ràng`.
- No-match: `Niko, quên fact anh thích bánh màu cầu vồng`.

Kỳ vọng cần nhìn trên dashboard:

- Tab `Traces` có khối `Loop Steps` cho turn vừa test.
- `Loop Steps` có các event chính: `loop_started`, `loop_decision`,
  `loop_tool_call_started`, `loop_tool_call_finished`, `loop_final_answer`.
- Tool step hiển thị được `tool_name`, `iteration`, `mutates_state`, `ok` và
  `error` nếu có.
- Tab `Bots` có runtime log source `loop`, ví dụ `loop_decision` hoặc
  `loop_tool_call_finished`, message đọc được tool và trạng thái.
- Raw JSON trace vẫn còn bên dưới để debug sâu.

Kết quả tự động trước live test ban đầu:

- Targeted tests cho loop/dashboard/correction đã pass.
- Full suite đã pass.

Kết quả live test: pass, có một chỉnh sửa guardrail nhỏ sau khi soi log.

- Ba smoke case đã chạy đủ: ambiguous delete, update có replacement nhưng còn
  nhiều fact khớp, và no-match.
- Cả ba turn đều ghi được `loop_started`, `loop_decision`,
  `loop_tool_call_started`, `loop_tool_call_finished` và `loop_final_answer`.
- Dashboard đã hiện khối `Loop Steps`; tab `Bots` có runtime log `source=loop`
  với message ngắn cho decision/tool/result.
- Kết quả hành vi đúng kỳ vọng: ambiguous case chỉ hỏi chọn ID và tạo pending,
  update ambiguous chưa mutate DB khi chưa có ID rõ, no-match không gọi
  update/delete tool.
- Khi soi trace phát hiện yêu cầu sửa/xóa mới vẫn mang pending metadata từ
  pending trước đó. Đã sửa `MemoryCorrectionWorkflow` để pending cũ chỉ áp dụng
  cho reply chọn ID thuần; yêu cầu sửa/xóa mới clear pending trước khi gọi
  Decision Model.
- Live retest sau restart: pass. Lượt ambiguous delete tạo pending xóa; lượt
  sửa/xóa mới tiếp theo có `pending_choices=[]`, không mang workflow cũ, và tạo
  pending sửa mới khi nhiều fact khớp. Follow-up chọn ID dùng
  `memory_correction_context_fallback`, apply đúng `update_fact` và ghi
  `memory_correction_pending_resolved`; không có `delete_fact` ở lượt resolve.
- Đã chỉnh `MemoryCorrectionLoopWorkflow` để runtime log của loop đi cùng
  thư mục state của `trace_logger`; test dùng trace tạm không ghi dữ liệu giả
  vào log dashboard thật.
- Verification tại thời điểm khóa Phase 5: targeted tests liên quan
  memory/loop/dashboard pass `87 passed`; full suite pass `184 passed`.
