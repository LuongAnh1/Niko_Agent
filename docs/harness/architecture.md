# Kiến Trúc Niko Agent

Niko Agent là một harness local cho AI agent. Mục tiêu của repo là có một hệ thống chạy thật: nhận prompt từ Telegram, định tuyến qua Fast/Deep agent, inject memory context cho Deep agent, ghi trace, lưu memory cơ bản và quan sát được trên dashboard.

## Mục Tiêu Hiện Tại

- Có Telegram gateway mỏng để nhận/gửi message.
- Có khung điều phối agent độc lập với gateway.
- Có hai chế độ xử lý:
  - `single`: đẩy thẳng vào Deep agent.
  - `two_agent`: dùng Fast agent để trả lời nhanh/triage, Deep agent để xử lý tác vụ cần phân tích.
- Có baseline memory gồm `chat_log`, `facts`, `episodes`.
- Có trace JSONL để dashboard và báo cáo có dữ liệu thực tế.
- Có Mini Niko Ops dashboard để nhìn luồng harness và quản lý facts.

## Kiến Trúc Tổng Quan

```text
Telegram
  -> bots/telegram
  -> ChatGatewayMessage
  -> ChatReplyGraph
     -> local rule
     -> Ollama/Nimble decision triage
     -> Fast Agent reply/final
     -> Deep Agent background
        -> Memory retrieval
        -> fcc-claude local runtime
  -> Telegram reply

Harness side effects:
  -> JSONL trace
  -> SQLite chat_log / facts / episodes
  -> Mini Niko Ops dashboard
```

Dashboard đang biểu diễn các khối chính:

```text
Gateway -> Router -> Fast Agent -> Reply
                 \-> Memory Gate -> Loop/Deep Agent -> Reply

Memory Gate -> Memory Records
Reply/turn events -> Trace/Ops
```

Trong đó `Tool Slot` mới là vị trí dự kiến cho vòng sau, chưa phải tool router hoàn chỉnh.
Thiết kế Loop tổng quát nằm ở `docs/loop/architecture.md`; baseline hiện tại
chưa có package `niko/loop/` production.

## Bố Cục Repo

```text
bots/
  decision_model/
    client.py          # Ollama /v1/systemone client cho Nimble decision model
    triage.py          # Mapping prompt Telegram sang reply_now/send_to_deep
    sticker.py         # Mapping prompt/reply sang mood sticker Telegram
    memory/            # Retrieval/write/candidate/correction decisions cho chat memory
    warmup.py          # Giữ Nimble loaded với keep_alive=-1
  telegram/
    bot.py             # Telegram gateway: polling, auth, mention filter, /id, reply, sticker
    instance_guard.py  # Single-instance lock/PID guard cho Telegram long polling
    sticker_picker.py  # Chọn file_id theo mood sticker đã quyết định
    stickers/
      ducks.json       # Mapping mood sang sticker Telegram

niko/
  runtime.py           # Gọi Claude CLI, đọc hook, build prompt, inject memory context
  config.py            # Load bootstrap env, runtime config và resolve project path
  chat_gateway.py      # ChatGatewayMessage, identity, alias, allowed user parsing
  HOOK.md              # Persona/hook nạp vào Niko
  harness/
    trace.py           # JSONL turn/event tracing
    runtime_log.py     # JSONL runtime log cho tab Bots
  memory/
    store.py           # SQLite store: chat_log, facts, episodes, FTS/fallback search
    runtime.py         # MemoryRuntime điều phối retrieval/write/correction, consolidation và format context
    context.py         # Dataclass, formatter và wrapper tương thích
    working_memory.py  # Recent conversation window tạm thời cho Deep/correction
    correction_workflow.py # Workflow sửa/xóa fact qua chat
    consolidation.py   # Scaffold đọc/mark batch chat_log chưa consolidated
  ops/
    dashboard.py       # HTTP server/entrypoint mỏng cho Niko Ops dashboard
    bots.py            # Start/stop Telegram bot, warmup/stop Decision Model
    config_schema.py   # Schema, validate, snapshot cho dashboard Config
    frontend.py        # Load HTML template
    templates/
      dashboard.html   # HTML/CSS/JS của dashboard
  graphs/
    chat_reply/
      graph.py         # ChatReplyGraph điều phối flow chat
      router.py        # Rule router local/deep/fast/busy
      prompts.py       # Prompt task cho Fast Agent và final compose
```

## Ranh Giới Trách Nhiệm

`bots/telegram` là gateway. Nó chỉ nên biết Telegram API, message shape, mention filter, `/id`, auth, send message và send sticker. Gateway không quyết định agent nào xử lý và không chứa logic memory.

`niko.chat_gateway` chuẩn hóa input từ gateway thành `ChatGatewayMessage`. Nếu sau này thêm Zalo/Discord/CLI, gateway mới nên convert message về cùng abstraction này.

`niko.graphs.chat_reply` là graph nghiệp vụ chat. Nó quyết định route local/fast/deep/busy, quản lý deep job background, ghi trace, ghi chat log và episode sau deep job.

`niko.runtime` là lớp gọi Claude CLI. Nó đọc env command, resolve `CLAUDE_WORKDIR`, nạp `niko/HOOK.md`, chèn identity context, gọi `MemoryRuntime` để lấy memory context rồi gọi `fcc-claude`.

`niko.memory` là memory baseline. `MemoryRuntime` là cổng điều phối retrieval
gate, retrieval modes (`search/list/recent/none`), recent working memory, write
gate, correction facade, manual/auto consolidation và format context cho Deep.
`MemoryCorrectionWorkflow` giữ pending state/mutate guardrail cho Phase 5 V1.
Store hiện dùng SQLite local, có FTS5 nếu môi trường SQLite hỗ trợ và fallback
search nếu không có FTS5.

Consolidation có đường manual qua dashboard/API (`Refresh batch`, `Run once`) và
đường auto default-off sau complete exchange. Auto chỉ chạy khi bật
`NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED=1`.

`niko.harness` và `niko.ops` là lớp quan sát/vận hành. Dashboard đọc memory/trace, không tham gia trực tiếp vào agent loop.

Loop mục tiêu sẽ là lớp tool workflow độc lập với Telegram. `ChatReplyGraph` chỉ
gọi Loop khi cần workflow nhiều bước như memory correction hoặc Jira retrieval;
tool không tự gửi reply Telegram và mọi mutate phải đi qua guardrail Python có
trace. Phase đầu nên dùng Python-controlled loop vì runtime hiện gọi Claude qua
`fcc-claude` CLI, chưa có native tool-use API ổn định trong application code.

## Import Chính

```python
from niko.graphs.chat_reply import ChatReplyGraph
```

Telegram gateway đang tạo một instance global:

```python
CHAT_REPLY_GRAPH = ChatReplyGraph()
```

## Config Chính

Luồng vận hành chính là dashboard-first:

1. Chạy `python -m niko.ops.dashboard`.
2. Chỉnh cấu hình trong tab Config.
3. Start Telegram bot và warmup/stop Decision Model trong tab Bots.

Các nhóm config chính trong dashboard:

- `Telegram Gateway`: token, allowlist, group mode, timeout/retry.
- `Agent Commands`: Claude/Fast command, workdir, timeout, hook file, identity.
- `Decision Model`: Ollama/Nimble base URL, model, triage/warmup/stop timeout, keep alive.
- `Sticker`: bật/tắt sticker, sticker set/config/mode, timeout.
- `Memory & Trace`: memory, recent context budget, retrieval/write/correction gate, trace, runtime log.
- `Replies`: suffix, wait/busy/error text.

Tab Config trong dashboard ghi runtime override vào `niko/.runtime/config.json`.
Khi cần thao tác nhanh, có thể sửa trực tiếp file JSON này rồi refresh dashboard;
với key được đọc lúc process start như token, allowlist hoặc agent command thì
restart Telegram Bot trong tab Bots.
Niko không còn dùng `niko/.env` hoặc `bots/telegram/.env`; chỉ root `.env` còn
vai trò bootstrap dashboard (`NIKO_OPS_HOST`, `NIKO_OPS_PORT`,
`NIKO_RUNTIME_CONFIG_FILE`). Thứ tự cấu hình hiệu lực là: OS env thật -> runtime
config -> root `.env` bootstrap -> default trong code. Secret/token có thể lưu
trong runtime config local; dashboard mask secret trong snapshot/API.

## Dữ Liệu Runtime

```text
niko/.runtime/
  config.json                      # Runtime config do dashboard Config ghi
  niko_memory.sqlite3              # SQLite memory
  traces/YYYY-MM-DD.jsonl          # JSONL trace
  logs/YYYY-MM-DD.jsonl            # Runtime log cho tab Bots
  claude_sandbox/                  # CLAUDE_WORKDIR
```

Thư mục `niko/.runtime/` là dữ liệu local, không commit. Nếu cần reset demo thì có thể dừng bot/dashboard rồi xóa hoặc backup các file runtime liên quan.

## Trạng Thái Baseline

Đã có:

- Gateway Telegram chạy thật.
- Fast/Deep agent flow.
- Memory retrieval cho Deep agent.
- Recent working-memory window cho Deep prompt theo `conversation_id`.
- Retrieval/write gate bằng local Decision Model, gồm mode inventory qua
  `fact_mode`/`episode_mode`.
- Memory correction V1 qua chat: nhận diện sửa/xóa fact, hỏi lại khi mơ hồ và
  update/delete SQLite có trace. Đây là lớp tạm trước khi có Loop/tool workflow.
- Manual semantic facts qua dashboard.
- Episodic record sau deep job và consolidation batch từ `chat_log`.
- JSONL trace và Mini Ops dashboard.

Chưa có:

- Tool router hoàn chỉnh.
- Long-running loop nhiều bước có tool execution.
- Automatic semantic extraction đáng tin cậy từ mọi đoạn chat.
- Embedding/rerank/graph reasoning.
- Lakehouse/Knowledge Graph production layer cho Jira/business data.

## Hướng Mở Rộng

- Thêm gateway mới: tạo folder trong `bots/`, parse message về `ChatGatewayMessage`, rồi gọi `ChatReplyGraph`.
- Thêm nghiệp vụ mới: tạo graph mới trong `niko/graphs/`.
- Thêm tool/loop: dùng `Tool Slot` hiện có như điểm mở rộng, nhưng giữ Telegram gateway mỏng.
- Khi triển khai Loop, đặt core trong `niko/loop/`, giữ tool adapters gần domain
  sở hữu dữ liệu như `niko/memory/` hoặc Jira lane.
- Cải tiến chat memory: làm chắc retrieval/write gate, consolidation và correction trên SQLite local trước.
- Cải tiến memory nghiệp vụ: nối sang lane lakehouse/Jira qua retrieval/tool slot khi cần dữ liệu issue/tài liệu, không trộn vào chat memory v1.
