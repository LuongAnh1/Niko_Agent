# Niko Harness Memory & Ops

Tài liệu này mô tả baseline harness của Niko: trace JSONL, SQLite memory và Mini Niko Ops dashboard. Mục tiêu là có một hệ thống chạy thật, quan sát được, có dữ liệu runtime để sau này cải tiến memory.

Checklist kiểm tra live qua Telegram nằm ở [Memory Live Verification](memory-live-verification.md).
Kịch bản eval deterministic nằm ở [Chat Memory Eval Scenarios](memory-eval-scenarios.md).

## Thành Phần

- `niko/harness/trace.py`: ghi trace JSONL theo turn và event.
- `niko/harness/runtime_log.py`: ghi runtime log JSONL cho tab Bots.
- `niko/memory/store.py`: SQLite memory store.
- `niko/memory/runtime.py`: `MemoryRuntime` điều phối retrieval gate, retrieval modes (`search/list/recent/none`), recent working-memory context, write gate, correction facade, search/list store, consolidation scaffold và format context.
- `niko/memory/context.py`: dataclass/result, formatter và wrapper tương thích cho code cũ.
- `niko/memory/working_memory.py`: dựng recent conversation window tạm thời từ `chat_log` cho Deep prompt và correction context.
- `niko/memory/correction_workflow.py`: workflow sửa/xóa fact qua chat, gồm pending choices, validate ID, update/delete và correction trace/log.
- `niko/memory/consolidation.py`: scaffold đọc batch `chat_log` chưa consolidated và mark-done có kiểm soát.
- `niko/ops/dashboard.py`: HTTP server/entrypoint mỏng cho dashboard.
- `niko/ops/bots.py`: start/stop Telegram Bot, warmup/stop Decision Model và snapshot tab Bots.
- `niko/ops/config_schema.py`: schema, validate, mask secret và snapshot cho tab Config.
- `niko/ops/frontend.py` + `niko/ops/templates/dashboard.html`: frontend dashboard tách khỏi server Python.

## Runtime State

Mặc định state nằm trong:

```text
niko/.runtime/
  niko_memory.sqlite3
  traces/YYYY-MM-DD.jsonl
  logs/YYYY-MM-DD.jsonl
  config.json
  claude_sandbox/
```

`niko/.runtime/` không nên commit. Đây là dữ liệu local để demo, debug và làm nguồn phân tích baseline.

## Memory Model

SQLite hiện có ba nhóm dữ liệu:

| Bảng | Loại memory | Ý nghĩa |
| --- | --- | --- |
| `chat_log` | operational log | Lưu user/assistant message theo conversation |
| `facts` | Semantic Memory | Fact, rule, preference, thông tin ổn định |
| `episodes` | Episodic Memory | Sự kiện/tác vụ theo thời gian, nhất là deep job hoàn tất |

`chat_log` không phải Semantic/Episodic Memory theo nghĩa dùng để suy luận. Nó là log vận hành để xem lại hội thoại. Semantic/Episodic hiện nằm ở `facts` và `episodes`.
`chat_log` hiện có cờ `consolidated` để scaffold consolidation biết batch nào đã
được xử lý xong. Manual consolidation v1 đã có thể tạo candidate bảo thủ, gọi
Decision Model để phân loại, rồi ghi facts/episodes với source `consolidation`;
threshold/scheduler tự động vẫn là phase sau.

Nếu SQLite hỗ trợ FTS5, store dùng full-text search. Nếu không có FTS5, store fallback về LIKE search có giới hạn.

## Retrieval

Deep agent nhận memory context khi:

```env
NIKO_MEMORY_ENABLED=1
NIKO_MEMORY_RETRIEVAL_ENABLED=1
NIKO_MEMORY_GATE_ENABLED=0
```

Flow retrieval:

```text
user prompt
  -> optional memory_retrieval_gate
  -> MemoryRuntime.retrieve_for_deep
  -> recent chat window from chat_log
  -> search/list facts
  -> search/list episodes
  -> format working/long-term memory sections
  -> inject vào prompt Deep agent
```

`retrieve_memory_context(...)` vẫn tồn tại như wrapper tương thích, nhưng cổng chính của pipeline là `MemoryRuntime`. Cách tách này giống tinh thần Waku hơn: runtime/graph chỉ gọi một memory pipeline, còn policy gate/search/format nằm trong memory layer.

Khi `NIKO_MEMORY_GATE_ENABLED=1`, câu hỏi inventory như "đang lưu fact nào" cũng
đi qua Decision Model. Nimble có thể trả `list_facts`, `recent_episodes`, hoặc
mode `fact_mode=list` / `episode_mode=recent`; Python chỉ thực thi mode đó bằng
SQLite store. Runtime có một guardrail hẹp sau model: nếu model đã mở
`decision=retrieve` nhưng để mode mặc định là `search` cho một câu inventory rõ
ràng, retrieval layer ép mode thực thi về `fact_mode=list` hoặc
`episode_mode=recent` để tránh search rỗng kiểu `search_facts("fact")`.

Fast triage không nhận memory context để giữ JSON sạch.

Recent conversation là working memory ngắn hạn, không phải long-term facts/episodes.
Nó được dựng lại từ `chat_log` theo `conversation_id`, bỏ chính incoming prompt
nếu prompt đó vừa được log, và nằm trong section `Recent conversation` trước khi
Deep chạy. Retrieval gate chỉ quyết định long-term facts/episodes; nếu gate chọn
`skip`, Deep vẫn có thể nhận recent conversation để hiểu follow-up trong cùng
cuộc trò chuyện.

Các key liên quan trong dashboard Config:

- `NIKO_MEMORY_RECENT_TURNS`: số lượt recent chat tối đa inject.
- `NIKO_MEMORY_RECENT_CHAR_BUDGET`: ngân sách ký tự cho section `Recent conversation`.
- `NIKO_MEMORY_LONG_TERM_CHAR_BUDGET`: ngân sách ký tự cho facts/episodes trong prompt.

## Memory Write

Khi một turn được xử lý:

- `chat_log` có thể được ghi cho incoming user message và assistant reply.
- Nếu Deep job hoàn tất, `MemoryRuntime` có thể tạo một episode cơ bản gồm prompt, answer và followups.
- `NIKO_MEMORY_WRITE_GATE_ENABLED=1` bật Nimble write gate để quyết định Deep episode nào đáng lưu dài hạn.
- Write gate chỉ chặn `episodes`; `chat_log` vẫn là operational log để debug/dashboard.
- Lượt chỉ inspect/list memory, ví dụ hỏi Niko đang lưu fact nào, nên được write gate
  chọn `discard` để không ghi thêm episodic memory nhiễu cho chính thao tác xem memory.
- Nếu write gate lỗi, Niko fail-open và vẫn ghi episode baseline.
- Semantic facts hiện chủ yếu được thêm thủ công qua dashboard hoặc API.

## Manual Consolidation

Dashboard Memory tab có khối `Consolidation` để chạy thủ công một batch
`chat_log` chưa consolidated. Luồng này tạo candidate bằng rule bảo thủ, dùng
Nimble chỉ để phân loại `semantic_fact`, `episodic_event` hoặc `discard`, rồi mới
ghi vào `facts`/`episodes` với `source=consolidation`.

Guardrail hiện tại:

- Candidate builder không suy diễn fact mơ hồ; semantic fact cần câu user nói rõ
  kiểu “ghi nhớ”, “lưu fact”, “từ giờ”.
- Nếu classifier/Ollama lỗi, batch không bị mark consolidated để có thể retry.
- Nếu classifier trả `discard`, batch có thể được mark done mà không tạo memory dài hạn.
- Chưa có scheduler nền; mọi consolidation v1 chạy qua API/dashboard thủ công.

Các event trace liên quan:

- `memory_retrieval`
- `memory_gate_decision`
- `memory_gate_error`
- `memory_write_chat_log`
- `memory_write_decision`
- `memory_write_gate_error`
- `memory_write_episode`
- `memory_write_error`

Với retrieval gate, trace/runtime log cần đọc cùng lúc `gate_decision`,
`gate_label`, `gate_query`, `gate_fact_mode` và `gate_episode_mode`. Inventory
chung có thể để `query` rỗng nhưng vẫn đúng nếu mode là `list` hoặc `recent`.
Trace `memory_retrieval` cũng có `recent_turn_count` để biết Deep có nhận
working memory gần đây không.

## Mini Niko Ops Dashboard

Chạy:

```bash
python -m niko.ops.dashboard
```

Mặc định:

- Host: `127.0.0.1`
- Port: `7777`
- URL: `http://127.0.0.1:7777`

Dashboard có các tab:

- Overview: live harness graph, preview facts/episodes, trace tail.
- Bots: start/stop/restart Telegram bot, chặn start trùng khi có instance external, warmup/stop Decision Model, xem runtime log dạng bảng.
- Memory: thêm/xóa semantic facts, xem semantic facts và episodic events.
- Chat: xem recent chat log.
- Traces: xem JSONL trace event.
- Config: chỉnh runtime config theo nhóm, gồm Telegram token, agent commands, Nimble, sticker, memory và reply text.
- Ops: xem endpoint và ranh giới baseline.

Dashboard chỉ nên chạy local trong v1. Nếu expose ra ngoài máy cá nhân thì cần thêm auth/reverse proxy.

## Dashboard Graph

Live graph không phải là graph mining. Đây là đồ thị quan sát harness runtime.

Ý nghĩa tuyến chính:

- `Gateway -> Router -> Reply`: local/busy reply.
- `Gateway -> Router -> Fast Agent -> Reply`: Fast trả lời ngay.
- `Gateway -> Router -> Memory Gate -> Loop/Deep Agent -> Reply`: Deep path.
- `Memory Gate -> Memory Records`: có retrieval từ facts/episodes.
- `Reply/turn events -> Trace/Ops`: trace/dashboard quan sát.

Turn vừa kết thúc được giữ sáng thêm một khoảng ngắn để dễ quan sát đường đi.

## API

- `GET /api/snapshot`
- `GET /api/bots`
- `GET /api/config`
- `GET /api/runtime/logs`
- `POST /api/config`
- `POST /api/config/reset`
- `POST /api/bots/{id}/{action}`
- `GET /api/runtime/bot`
- `POST /api/runtime/bot/start`
- `POST /api/runtime/bot/stop`
- `GET /api/traces`
- `GET /api/memory`
- `GET /api/memory/consolidation`
- `POST /api/memory/facts`
- `POST /api/memory/consolidation/run`
- `DELETE /api/memory/facts/{id}`

`POST /api/bots/decision/warmup` trả về ngay trạng thái `warming` và chạy warmup
nền với `NIKO_DECISION_MODEL_WARMUP_TIMEOUT_SECONDS`. `POST
/api/bots/decision/stop` gửi unload request tới Ollama để gỡ model khỏi
RAM/VRAM mà không cần tắt Ollama daemon.

Ví dụ thêm fact:

```bash
curl -X POST http://127.0.0.1:7777/api/memory/facts ^
  -H "Content-Type: application/json" ^
  -d "{\"subject\":\"Project\",\"content\":\"Niko is a local agent harness.\"}"
```

## Config

Dashboard là luồng chính để chỉnh config. Chỉ root `.env` còn nên tồn tại, và chỉ nên giữ bootstrap tối thiểu như `NIKO_OPS_HOST`, `NIKO_OPS_PORT`, `NIKO_RUNTIME_CONFIG_FILE`. Không dùng `niko/.env` hoặc `bots/telegram/.env`; secret như `TELEGRAM_BOT_TOKEN` nên nhập trong dashboard. API snapshot sẽ mask secret và file runtime nằm trong `niko/.runtime/`.

`niko/ops/config_schema.py` là danh sách trắng các key được phép ghi từ UI/API.
Nếu muốn thêm config mới vào dashboard, thêm field vào schema này trước rồi mới
đọc key đó trong runtime tương ứng.

Nếu không muốn đi qua UI, có thể sửa trực tiếp `niko/.runtime/config.json`. File
này là JSON object đơn giản, mỗi key là tên biến cấu hình trong tab Config và
value nên để dạng string. Sau khi sửa trực tiếp, refresh dashboard; với các key
được đọc khi process start như Telegram token, allowlist, Claude/Fast command,
nên restart Telegram Bot trong tab Bots.

## Baseline Có Ý Nghĩa Gì

Baseline hiện tại giúp chứng minh:

- Agent có thể chạy qua một harness quan sát được.
- Memory context có thể được retrieve và inject vào Deep agent.
- Có dữ liệu chat/trace/episode để phân tích sau.
- Dashboard có thể dùng để demo luồng hoạt động.

Baseline cũng cố tình bộc lộ điểm yếu:

- Search còn text-based.
- Facts chưa được tự trích xuất ổn định.
- Episodes còn là summary đơn giản.
- Chưa có schema graph/Knowledge Graph.
- Chưa có lakehouse storage layer cho Jira/business data.

Các điểm yếu này là lý do để phát triển chat memory local trước, rồi nối sang
memory backend/lakehouse/graph riêng khi cần dữ liệu Jira hoặc tài liệu nghiệp vụ.

## Ghi Chú Về Waku

Niko kế thừa ý tưởng từ Waku: memory store local, semantic/episodic separation, JSONL traces và dashboard quan sát. Niko không dùng provider abstraction, pricing, eval arena hay API loop của Waku trong v1, vì runtime chính vẫn là `fcc-claude` trên máy local.
## Chat Memory Correction Gate

Từ Phase 5 V1, Niko có một gate riêng để nhận diện lệnh sửa hoặc xóa chat memory qua Telegram.
Gate này dùng local Decision Model/Nimble để chọn intent, nhưng không cho model tự ghi DB. Đây là
baseline tạm thời để demo chat memory an toàn; khi Loop/tool slot hoàn chỉnh hơn, phần xác nhận,
chọn target và mutate memory nên chuyển thành workflow/tool riêng có state bền hơn.

Code Decision Model cho chat memory đã được tách vào `bots/decision_model/memory/`:

- `retrieval.py`: quyết định đọc/search/list facts hoặc recent episodes trước Deep.
- `write.py`: quyết định có ghi Deep result thành episodic memory không.
- `candidate.py`: phân loại candidate do consolidation tạo ra.
- `correction.py`: nhận diện intent sửa/xóa memory; đây là file chính cần soi khi correction gate bắt nhầm câu inventory.
- `__init__.py`: giữ API cũ `bots.decision_model.memory` để các import hiện tại không phải đổi.

Workflow thực thi nằm ở `niko/memory/correction_workflow.py`; `MemoryRuntime`
chỉ còn facade để `ChatReplyGraph` không phải biết pending state hay thao tác
mutate SQLite.

- `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=0` mặc định tắt để tránh sửa/xóa bất ngờ trong demo.
- Khi bật, Decision Model chỉ trả về `none`, `correct_memory` hoặc `forget_memory`, kèm `query`, `target_type`, `replacement` nếu có.
- Python runtime search facts bằng query/ID, chỉ update/delete khi match đúng một fact rõ ràng.
- Nếu match nhiều fact, thiếu replacement hoặc target là episode, Niko hỏi lại hoặc báo read-only thay vì mutate.
- Các event quan sát chính là `memory_correction_decision`, `memory_correction_applied`, `memory_correction_clarify`.
- Nếu Nimble chọn sai giữa `forget_memory` và `correct_memory`, guardrail theo marker trong prompt có thể sửa intent
  cuối cùng; nếu `correct_memory` thiếu `replacement`, runtime trích phần sau `thành`/`thay bằng` như lớp an toàn hẹp.

### Decision context cho correction

Correction gate không nên nhìn từng tin nhắn rời rạc. Runtime dựng một context ngắn, có cấu trúc trước khi gọi
Decision Model:

- `current_prompt`: tin nhắn hiện tại.
- `recent_turns`: ngữ cảnh chính cho model, gồm vài dòng chat gần nhất trong cùng conversation, đã truncate và bỏ chính incoming prompt hiện tại.
- `active_workflow`: metadata phụ, ví dụ `memory_correction` khi lượt trước Niko vừa hỏi anh chọn fact ID.
- `pending_action`: metadata phụ cho thao tác đang chờ, như `forget_memory` hoặc `correct_memory`.
- `pending_choices`: metadata phụ cho danh sách ID/type mà runtime cho phép chọn, ví dụ `fact #8`, `fact #6`.
- `pending_replacement`: metadata phụ cho nội dung thay thế nếu đang ở luồng sửa fact.

Decision Model phải phân loại dựa trên `current_prompt` và `recent_turns` trước. Các trường `pending_*` chỉ giúp model hiểu
rằng câu rất ngắn như `fact #8 nhé` đang nối tiếp câu hỏi trước đó, chứ không thay thế ngữ cảnh hội thoại. Python vẫn
validate ID có nằm trong `pending_choices` và chỉ Python mới được update/delete SQLite. Nếu model vẫn trả `none` hoặc lỗi,
runtime có fallback guardrail cho reply dạng fact ID sau một pending workflow, nhưng fallback này chỉ là lớp an toàn cuối.
Trong trường hợp model đã có context nhưng vẫn phân loại sai một reply chỉ chọn ID, ví dụ đang chờ `forget_memory` nhưng
model trả `correct_memory`, runtime sẽ ưu tiên `pending_action` cũ và ghi `model_decision` vào trace để debug.
Với câu chỉ đọc/list memory như `hiện tại em có những fact gì về anh`, `correction.py` có read-only guardrail để ép
decision về `none` nếu Nimble lỡ trả `correct_memory`; lượt đó sẽ được nhường lại cho retrieval gate (`fact_mode=list`).
