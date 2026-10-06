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

## Bố Cục Repo

```text
bots/
  decision_model/
    client.py          # Ollama /v1/systemone client cho Nimble decision model
    triage.py          # Mapping prompt Telegram sang reply_now/send_to_deep
    warmup.py          # Giu Nimble loaded voi keep_alive=-1
  telegram/
    bot.py             # Telegram gateway: polling, auth, mention filter, /id, reply, sticker
    sticker_picker.py  # Local sticker picker cho Telegram Duck
    stickers/
      ducks.json       # Mapping mood/keyword sang sticker Telegram

niko/
  runtime.py           # Gọi Claude CLI, đọc hook, build prompt, inject memory context
  config.py            # Load env và resolve project path
  chat_gateway.py      # ChatGatewayMessage, identity, alias, allowed user parsing
  HOOK.md              # Persona/hook nạp vào Niko
  harness/
    trace.py           # JSONL turn/event tracing
  memory/
    store.py           # SQLite store: chat_log, facts, episodes, FTS/fallback search
    context.py         # Retrieve memory context cho Deep agent
  ops/
    dashboard.py       # Mini Niko Ops dashboard
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

`niko.runtime` là lớp gọi Claude CLI. Nó đọc env command, resolve `CLAUDE_WORKDIR`, nạp `niko/HOOK.md`, chèn identity context, retrieve memory context rồi gọi `fcc-claude`.

`niko.memory` là memory baseline. Store hiện dùng SQLite local, có FTS5 nếu môi trường SQLite hỗ trợ và fallback search nếu không có FTS5.

`niko.harness` và `niko.ops` là lớp quan sát/vận hành. Dashboard đọc memory/trace, không tham gia trực tiếp vào agent loop.

## Import Chính

```python
from niko.graphs.chat_reply import ChatReplyGraph
```

Telegram gateway đang tạo một instance global:

```python
CHAT_REPLY_GRAPH = ChatReplyGraph()
```

## Env Chính

Root `.env` giữ cấu hình chung:

- `CLAUDE_CLI_COMMAND`
- `CLAUDE_DEEP_AGENT_COMMAND`
- `CLAUDE_WORKDIR`
- `CLAUDE_TIMEOUT_SECONDS`
- `CHAT_IDENTITY_ENABLED`
- `CHAT_ALLOWED_USER_KEYS`
- `CHAT_USER_ALIASES`

`niko/.env` giữ cấu hình agent/harness:

- `NIKO_AGENT_MODE`
- `NIKO_DECISION_MODEL_ENABLED`
- `NIKO_DECISION_MODEL_BASE_URL`
- `NIKO_DECISION_MODEL_NAME`
- `NIKO_DECISION_MODEL_TIMEOUT_SECONDS`
- `NIKO_DECISION_MODEL_KEEP_ALIVE`
- `NIKO_FAST_AGENT_COMMAND`
- `NIKO_FAST_AGENT_TIMEOUT_SECONDS`
- `NIKO_UNCERTAIN_DELAY_SECONDS`
- `NIKO_DEEP_WAIT_REPLY`
- `NIKO_DEEP_BUSY_REPLY`
- `NIKO_PROMPT_HOOK_FILE`
- `NIKO_REPLY_SUFFIX`
- `NIKO_TOOL_UNAVAILABLE_REPLY`
- `NIKO_STATE_DIR`
- `NIKO_TRACE_ENABLED`
- `NIKO_MEMORY_ENABLED`
- `NIKO_MEMORY_RETRIEVAL_ENABLED`
- `NIKO_MEMORY_WRITE_ENABLED`
- `NIKO_MEMORY_TOP_K`
- `NIKO_OPS_HOST`
- `NIKO_OPS_PORT`

`bots/telegram/.env` giữ cấu hình Telegram:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `TELEGRAM_DROP_PENDING_UPDATES`
- `TELEGRAM_REQUEST_TIMEOUT_SECONDS`
- `TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS`
- `TELEGRAM_STARTUP_RETRIES`
- `TELEGRAM_STARTUP_RETRY_DELAY_SECONDS`
- `TELEGRAM_GROUP_MODE`
- `TELEGRAM_MENTION_REPLIES`
- `TELEGRAM_STICKERS_ENABLED`
- `TELEGRAM_STICKER_CONFIG_FILE`
- `TELEGRAM_STICKER_SET_NAME`
- `TELEGRAM_STICKER_MODE`
- `TELEGRAM_STICKER_TIMEOUT_SECONDS`

Thứ tự load env hiện tại: root `.env` -> `niko/.env` -> `bots/telegram/.env`. Biến môi trường thật của OS vẫn ưu tiên hơn file `.env`.

## Dữ Liệu Runtime

```text
niko/.runtime/
  niko_memory.sqlite3              # SQLite memory
  traces/YYYY-MM-DD.jsonl          # JSONL trace
  claude_sandbox/                  # CLAUDE_WORKDIR
```

Thư mục `niko/.runtime/` là dữ liệu local, không commit. Nếu cần reset demo thì có thể dừng bot/dashboard rồi xóa hoặc backup các file runtime liên quan.

## Trạng Thái Baseline

Đã có:

- Gateway Telegram chạy thật.
- Fast/Deep agent flow.
- Memory retrieval cho Deep agent.
- Manual semantic facts qua dashboard.
- Episodic record sau deep job.
- JSONL trace và Mini Ops dashboard.

Chưa có:

- Tool router hoàn chỉnh.
- Long-running loop nhiều bước có tool execution.
- Automatic semantic extraction đáng tin cậy từ mọi đoạn chat.
- Embedding/rerank/graph reasoning.
- Lakehouse/Knowledge Graph production layer.

## Hướng Mở Rộng

- Thêm gateway mới: tạo folder trong `bots/`, parse message về `ChatGatewayMessage`, rồi gọi `ChatReplyGraph`.
- Thêm nghiệp vụ mới: tạo graph mới trong `niko/graphs/`.
- Thêm tool/loop: dùng `Tool Slot` hiện có như điểm mở rộng, nhưng giữ Telegram gateway mỏng.
- Cải tiến memory: đọc dữ liệu từ SQLite/JSONL baseline, chuẩn hóa thành lakehouse, dựng graph schema, rồi dùng graph/semantic retrieval để thay thế baseline search.
