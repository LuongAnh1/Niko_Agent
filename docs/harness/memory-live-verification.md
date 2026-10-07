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
| Write `remember` | `Lên kế hoạch sửa memory runtime để tuần sau anh demo với thầy.` | `memory_write_decision` có `decision=remember`; có `memory_write_episode`. |
| Consolidation fact | `Ghi nhớ rằng anh thích checklist có mục đích rõ ràng.` | Memory tab `Refresh batch` có candidate `semantic_fact`; `Run once` ghi fact source `consolidation`. |
| Consolidation discard | `haha oke` | Candidate `discard`; run once mark rows nhưng không ghi fact/episode. |

## Quan Sát Trên Dashboard

Trong tab `Bots` hoặc bảng runtime log, tìm các event:

- `memory_gate_decision`: có `decision`, `query`, `reason`, `confidence`, `model`.
- `memory_gate_error`: Ollama/Nimble lỗi; retrieval phải fail-open bằng raw prompt.
- `memory_retrieval`: có facts/episodes được retrieve và metadata gate.
- `memory_write_decision`: có `remember` hoặc `discard`.
- `memory_write_gate_error`: write gate lỗi; episode baseline vẫn được ghi nếu memory write bật.
- `memory_write_episode`: có episode mới khi write gate cho phép.
- `consolidation`: xem kết quả trong Memory tab sau khi bấm `Run once`.

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
- Write gate sau inventory chung: cần tinh chỉnh sau. Lượt inventory pass nhưng
  write gate chọn `remember` và ghi một episode mới cho chính câu inspect memory;
  đây không làm hỏng retrieval, nhưng về lâu dài nên hướng model `discard` các
  lượt chỉ liệt kê/kiểm tra memory để tránh nhiễu episodic memory.
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
- Sau mỗi test live cần ghi lại kết quả vào tài liệu này ngay, gồm pass/fail,
  trace/log đáng chú ý và chỉnh sửa phát sinh.

### Ghi Chú UI

- Trong tài liệu cũ gọi là `Preview consolidation`; trên dashboard hiện tại nút
  tương ứng là `Refresh batch`.
- `Refresh batch` chỉ đọc batch/candidate, không ghi memory.
- `Run once` mới gọi classifier, ghi fact/episode nếu được chọn, rồi mark đúng
  rows trong batch là consolidated.

## Khi Nào Coi Là Pass

- Dashboard Config hiển thị mô tả dễ hiểu cho các key memory/decision.
- Mỗi prompt mẫu tạo đúng event kỳ vọng trong Runtime Log hoặc Trace.
- Không có Telegram bot crash khi bật retrieval gate/write gate.
- Memory tab preview/run consolidation không ghi bừa khi prompt là small talk.
- Full test suite vẫn pass sau khi chỉnh config/docs.
