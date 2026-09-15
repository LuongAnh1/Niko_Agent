# Kiến Trúc Niko Agent

Niko Agent là project Python dùng Claude CLI thay cho API LLM trực tiếp. Gateway chính hiện tại là Telegram, còn luồng điều phối chat nằm trong `niko/graphs/chat_reply/`.

## Mục Tiêu Hiện Tại

- Nhận tin nhắn từ Telegram.
- Chỉ xử lý group message khi có tag bot, trừ lệnh `/id` và `/whoami`.
- Tách phần giao tiếp nhanh và phần xử lý sâu bằng hai agent.
- Gọi Claude CLI qua `fcc-claude` trong runtime sandbox.
- Chưa dùng Working Memory, RAG, Tool Router hay Memory Layer riêng.

## Bố Cục Repo

```text
bots/
  telegram/
    bot.py             # Telegram gateway: polling, auth, mention filter, /id, reply, sticker
    sticker_picker.py  # Local sticker picker cho Telegram Duck
    stickers/
      ducks.json       # Mapping mood/keyword sang sticker Telegram

niko/
  runtime.py           # Gọi Claude CLI, đọc hook, build prompt, deep agent command
  config.py            # Load env và resolve project path
  chat_gateway.py      # ChatGatewayMessage, identity, alias, allowed user parsing
  HOOK.md              # Persona/hook nạp vào Niko
  graphs/
    chat_reply/
      graph.py         # ChatReplyGraph điều phối flow chat
      router.py        # Rule router local/deep/fast/busy
      prompts.py       # Prompt task cho Fast Agent và final compose
```

## Ranh Giới Trách Nhiệm

`bots/telegram` là gateway. Nó chỉ nên biết Telegram API, message shape, mention filter, `/id`, auth, send message và send sticker. Gateway không nên chứa logic quyết định LLM nào xử lý.

`niko.graphs.chat_reply` là graph nghiệp vụ chat. Nó quyết định tin nhắn nào trả lời local, tin nào cho Fast Agent triage, tin nào đẩy Deep Agent, và cách compose kết quả sau khi Deep xong.

`niko.runtime` là lớp gọi Claude CLI. Nó đọc env command, resolve `CLAUDE_WORKDIR`, nạp `niko/HOOK.md`, chèn identity context nếu bật `CHAT_IDENTITY_ENABLED`, rồi gọi CLI.

`niko.chat_gateway` là abstraction chung cho các cổng chat. Nếu sau này thêm Zalo/Discord, gateway mới nên convert message về `ChatGatewayMessage` rồi gọi graph tương tự Telegram.

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

`niko/.env` giữ cấu hình agent:

- `NIKO_AGENT_MODE`
- `NIKO_FAST_AGENT_COMMAND`
- `NIKO_FAST_AGENT_TIMEOUT_SECONDS`
- `NIKO_UNCERTAIN_DELAY_SECONDS`
- `NIKO_DEEP_WAIT_REPLY`
- `NIKO_DEEP_BUSY_REPLY`
- `NIKO_PROMPT_HOOK_FILE`
- `NIKO_REPLY_SUFFIX`
- `NIKO_TOOL_UNAVAILABLE_REPLY`

`bots/telegram/.env` giữ cấu hình Telegram:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `TELEGRAM_DROP_PENDING_UPDATES`
- `TELEGRAM_GROUP_MODE`
- `TELEGRAM_MENTION_REPLIES`
- `TELEGRAM_STICKERS_ENABLED`
- `TELEGRAM_STICKER_CONFIG_FILE`
- `TELEGRAM_STICKER_SET_NAME`
- `TELEGRAM_STICKER_MODE`

Thứ tự load env hiện tại: root `.env` -> `niko/.env` -> `bots/telegram/.env`. Biến môi trường thật của OS vẫn ưu tiên hơn file `.env`.

## Hướng Mở Rộng

- Thêm gateway mới: tạo folder trong `bots/`, parse message về `ChatGatewayMessage`, rồi gọi `ChatReplyGraph`.
- Thêm nghiệp vụ mới: tạo graph mới trong `niko/graphs/`.
- Thêm memory/RAG/tool: nên thêm thành node/layer riêng trong graph, không đưa vào Telegram gateway.
- Nếu cần cấp tài liệu cố định cho agent đọc, nên để harness/memory/RAG chèn phần liên quan vào prompt thay vì để Claude CLI tự quét repo.
