# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Niko Agent là một AI agent harness chạy local. Repo này tập trung vào việc có một hệ thống chạy thật để quan sát luồng agent, ghi trace, lưu memory cơ bản, rồi dùng baseline đó làm nền cho hướng cải tiến Semantic/Episodic Memory.

Điểm quan trọng: Niko không gọi trực tiếp API LLM trong code. Runtime hiện tại gọi Claude CLI/FCC qua `fcc-claude` trên máy local.

## Niko Hiện Có Gì

- Telegram gateway: nhận/gửi tin qua Telegram, hỗ trợ group mention, `/id`, `/whoami`, allowlist, sticker.
- Two-agent chat flow:
  - Fast agent: phản hồi nhanh, triage, hoặc compose câu trả lời cuối.
  - Deep agent: xử lý tác vụ cần suy nghĩ lâu hơn qua `fcc-claude`.
- Harness tracing: ghi JSONL event theo từng turn.
- SQLite memory baseline:
  - `chat_log`: lịch sử hội thoại đã xử lý.
  - `facts`: Semantic Memory thủ công/baseline.
  - `episodes`: Episodic Memory sinh ra sau deep job.
- Mini Niko Ops dashboard: xem live harness graph, trace, chat log, memory; thêm/xóa facts; chỉnh runtime config.

## Cấu Trúc Chính

```text
bots/telegram/                  # Telegram gateway
bots/decision_model/            # Ollama/Nimble decision scripts cho fast triage
niko/chat_gateway.py             # Chuẩn hóa message thành ChatGatewayMessage
niko/graphs/chat_reply/          # Router, Fast/Deep handoff, final compose
niko/runtime.py                  # Gọi fcc-claude, nạp hook, inject identity/memory
niko/harness/trace.py            # Trace JSONL
niko/memory/store.py             # SQLite memory store
niko/memory/context.py           # Retrieve memory context cho Deep agent
niko/ops/dashboard.py            # Mini Niko Ops dashboard
niko/HOOK.md                     # Persona/hook của Niko
niko/.runtime/                   # Runtime state, không commit
docs/                            # Tài liệu kiến trúc, flow, demo, roadmap
```

## Chạy Nhanh

1. Cài và cấu hình FCC/Claude CLI để lệnh `fcc-claude` chạy được.
2. Cài Ollama, bật Ollama server, rồi pull model Nimble dùng cho decision triage:

```bash
ollama pull nimble
```

Nếu anh dùng tag khác, ví dụ một bản quantized cụ thể, đổi `NIKO_DECISION_MODEL_NAME` trong `niko/.env` cho khớp.

3. Copy các file env mẫu:

```bash
copy .env.example .env
copy niko\.env.example niko\.env
copy bots\telegram\.env.example bots\telegram\.env
```

4. Điền `TELEGRAM_BOT_TOKEN` trong `bots/telegram/.env`.
5. Lấy `chat_id` và `user_key` bằng `/id` hoặc `/whoami`.
6. Warm up Nimble để model được giữ loaded cho tới khi Ollama tắt:

```bash
rtk python -m bots.decision_model.warmup
ollama ps
```

Nếu `ollama ps` hiện `nimble:latest` với thời gian giữ loaded là `Forever`, decision model đã sẵn sàng.

7. Chạy bot:

```bash
python -m bots.telegram.bot
```

8. Chạy dashboard quan sát:

```bash
python -m niko.ops.dashboard
```

Dashboard mặc định: `http://127.0.0.1:7777`

Trong dashboard có tab `Config` để chỉnh các cấu hình vận hành như Nimble,
sticker, memory, reply text và bật/tắt bot Telegram do dashboard quản lý.
Secret/token vẫn để trong `.env`, không chỉnh trên dashboard.

## Cấu Hình Tối Thiểu

Root `.env`:

```env
CLAUDE_CLI_COMMAND=fcc-claude -p
CLAUDE_DEEP_AGENT_COMMAND=fcc-claude --bare --no-session-persistence --tools= -p
CLAUDE_WORKDIR=niko/.runtime/claude_sandbox
CLAUDE_TIMEOUT_SECONDS=180
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A
```

`niko/.env`:

```env
NIKO_AGENT_MODE=two_agent
NIKO_DECISION_MODEL_ENABLED=1
NIKO_DECISION_MODEL_BASE_URL=http://localhost:11434
NIKO_DECISION_MODEL_NAME=nimble
NIKO_DECISION_MODEL_TIMEOUT_SECONDS=10
NIKO_DECISION_MODEL_KEEP_ALIVE=-1
NIKO_FAST_AGENT_COMMAND=fcc-claude --model fable --bare --no-session-persistence --tools "" -p
NIKO_FAST_AGENT_TIMEOUT_SECONDS=45
NIKO_UNCERTAIN_DELAY_SECONDS=3
NIKO_PROMPT_HOOK_FILE=niko/HOOK.md
NIKO_REPLY_SUFFIX=Meow
NIKO_STATE_DIR=niko/.runtime
NIKO_TRACE_ENABLED=1
NIKO_MEMORY_ENABLED=1
NIKO_MEMORY_RETRIEVAL_ENABLED=1
NIKO_MEMORY_WRITE_ENABLED=1
NIKO_MEMORY_TOP_K=4
NIKO_OPS_HOST=127.0.0.1
NIKO_OPS_PORT=7777
```

Trước khi chạy bot, Ollama phải đang bật và model trong `NIKO_DECISION_MODEL_NAME`
phải pull sẵn trên máy. Lệnh warmup dưới đây gọi model một lần và gửi
`keep_alive=-1`, nên model được giữ loaded cho tới khi anh tắt Ollama:

```bash
rtk python -m bots.decision_model.warmup
ollama ps
```

Nếu muốn gỡ Nimble khỏi RAM/VRAM nhưng vẫn giữ Ollama chạy, dùng:

```bash
ollama stop nimble
ollama ps
```

Nếu bot vẫn đang chạy và `NIKO_DECISION_MODEL_KEEP_ALIVE=-1`, lần chat tiếp theo
cần decision model có thể load Nimble lại. Muốn tắt hẳn decision model thì đổi
env rồi restart bot:

```env
NIKO_DECISION_MODEL_ENABLED=0
TELEGRAM_STICKER_DECISION_MODEL_ENABLED=0
```

`bots/telegram/.env`:

```env
TELEGRAM_BOT_TOKEN=token_cua_bot
TELEGRAM_ALLOWED_CHAT_IDS=-100xxxxxxxxxx
TELEGRAM_GROUP_MODE=mentions
TELEGRAM_MENTION_REPLIES=1
TELEGRAM_STICKERS_ENABLED=1
TELEGRAM_STICKER_DECISION_MODEL_ENABLED=1
TELEGRAM_STICKER_DECISION_MODEL_TIMEOUT_SECONDS=5
TELEGRAM_STICKER_CONFIG_FILE=bots/telegram/stickers/ducks.json
TELEGRAM_STICKER_SET_NAME=UtyaDuck
TELEGRAM_STICKER_MODE=smart
TELEGRAM_STICKER_TIMEOUT_SECONDS=5
```

Sticker Telegram dùng Nimble local để chọn mood sau khi text reply đã gửi. Nếu
Nimble chọn `no_sticker` hoặc lỗi/timeout, bot chỉ bỏ qua sticker và không
fallback về keyword rule cũ.

Thứ tự load env file: root `.env` -> `niko/.env` -> `bots/telegram/.env`.
Thứ tự cấu hình hiệu lực: OS env thật -> `niko/.runtime/config.json` do tab
Config ghi -> env file -> default trong code. Nếu một key bị OS env khóa,
dashboard vẫn hiển thị nhưng không ghi đè được.

## Demo Baseline

Các kịch bản demo nhanh:

- Gửi `@Niko2_Bot em ơi`: route local/fast, dashboard sáng tuyến `Gateway -> Router -> Reply` hoặc `Gateway -> Router -> Fast Agent -> Reply`.
- Gửi câu có `fact`, `memory`, `phân tích`, `debug`: route deep, dashboard sáng `Memory Gate -> Loop -> Reply`.
- Thêm một fact trong dashboard, hỏi câu liên quan: Deep agent nhận memory context từ SQLite.
- Mở tab Traces để xem `turn_start`, `route_decision`, `memory_retrieval`, `turn_end`.

Chi tiết hơn xem [docs/demo-guide.md](docs/demo-guide.md).

## Tài Liệu

- [Kiến trúc](docs/architecture.md)
- [Luồng chat Telegram](docs/telegram-chat-flow.md)
- [Harness Memory & Ops](docs/niko-harness-memory-ops.md)
- [Nghiệp vụ harness](docs/business-domains/README.md)
- [Demo Guide](docs/demo-guide.md)
- [Memory Roadmap](docs/memory-roadmap.md)

## Ranh Giới Baseline

Repo này chưa phải hệ thống memory hoàn chỉnh. Baseline hiện tại cố ý đơn giản để phục vụ demo và đo điểm yếu:

- Semantic facts chủ yếu thêm thủ công qua dashboard.
- Retrieval là FTS/LIKE text search, chưa có embedding/rerank/graph reasoning.
- Episodic memory mới tóm tắt deep job, chưa tự trích xuất sự kiện giàu ngữ nghĩa.
- Tool/Loop slot đã có trên dashboard nhưng chưa phải tool router hoàn chỉnh.
- Lakehouse/Knowledge Graph sẽ là hướng cải tiến sau, đọc dữ liệu từ SQLite/JSONL baseline.

## Test

```bash
python -m unittest discover
```

Nếu chạy qua Codex/RTK:

```bash
rtk python -m unittest discover
```

## Ghi Chú Phát Triển

- Telegram gateway chỉ nên là cổng vào/ra, không chứa logic memory/LLM.
- Logic điều phối nằm trong `niko/graphs/chat_reply/`.
- Runtime gọi LLM nằm trong `niko/runtime.py`.
- Memory/trace/dashboard là harness baseline, không nên trộn vào gateway.
- `niko/.runtime/` là dữ liệu chạy local và không commit.
