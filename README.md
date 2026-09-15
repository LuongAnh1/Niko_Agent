# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Niko Agent dùng Claude CLI (`fcc-claude`) thay cho việc gọi API LLM trực tiếp. Bot Telegram chỉ là gateway; workflow trả lời chat nằm trong `niko/graphs/chat_reply/`, còn `niko/runtime.py` giữ phần gọi Claude CLI và hook/persona.

## Cấu Trúc

```text
niko/runtime.py                         # Claude CLI runtime, prompt hook, command config, deep call
niko/graphs/chat_reply/                 # Chat reply graph: route, fast triage, deep handoff, final compose
niko/.env.example                       # Cấu hình riêng của Niko runtime/agent
niko/.runtime/                          # Vùng chạy tạm của Niko, không commit
niko/HOOK.md                            # Persona/hook nạp vào Niko
bots/telegram/                          # Telegram gateway: polling, mention, /id, sticker, gửi/nhận tin
bots/telegram/stickers/ducks.json       # Mapping mood cho sticker Duck của Telegram
```

## Tài Liệu

- `docs/architecture.md`: tổng quan kiến trúc và ranh giới module.
- `docs/telegram-chat-flow.md`: luồng xử lý tin nhắn Telegram với hai agent.
- `AGENTS.md`: context ngắn cho Codex khi mở phiên chat mới.

## Chạy Local

1. Tạo bot Telegram bằng [@BotFather](https://t.me/BotFather) và lấy token.
2. Copy `.env.example` thành `.env`, điền cấu hình chung cho Claude/identity.
3. Copy `niko/.env.example` thành `niko/.env`, điền cấu hình riêng của Niko.
4. Copy `bots/telegram/.env.example` thành `bots/telegram/.env`, điền cấu hình Telegram.
5. Cài và cấu hình Free Claude Code/FCC. Nếu `fcc-claude` trỏ qua FCC thì chạy `fcc-server` trước.
6. Chạy bot:

```bash
python -m bots.telegram.bot
```

## Lấy ID

Dùng `/id` trong private chat hoặc `/id@TenBot` trong group để lấy `chat_id` và `user_key`. Trong group, mặc định bot chỉ xử lý tin nhắn có tag `@TenBot`. Khi trả lời, bot sẽ mention người vừa gọi nếu `TELEGRAM_MENTION_REPLIES=1`.

## Env

Root `.env` là cấu hình chung của hệ thống:

```env
CLAUDE_CLI_COMMAND=fcc-claude -p
CLAUDE_DEEP_AGENT_COMMAND=fcc-claude --bare --no-session-persistence --tools= -p
CLAUDE_WORKDIR=niko/.runtime/claude_sandbox
CLAUDE_TIMEOUT_SECONDS=180
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A
```

`niko/.env` là cấu hình riêng của Niko:

```env
NIKO_AGENT_MODE=two_agent
NIKO_FAST_AGENT_COMMAND=fcc-claude --model fable --bare --no-session-persistence --tools "" -p
NIKO_FAST_AGENT_TIMEOUT_SECONDS=45
NIKO_UNCERTAIN_DELAY_SECONDS=3
NIKO_PROMPT_HOOK_FILE=niko/HOOK.md
NIKO_REPLY_SUFFIX=Meow
```

`bots/telegram/.env` chỉ là cấu hình gateway Telegram:

```env
TELEGRAM_BOT_TOKEN=token_cua_bot
TELEGRAM_ALLOWED_CHAT_IDS=-100xxxxxxxxxx
TELEGRAM_GROUP_MODE=mentions
TELEGRAM_MENTION_REPLIES=1
TELEGRAM_STICKERS_ENABLED=1
```

Thứ tự load env: root `.env` -> `niko/.env` -> `bots/telegram/.env`. Biến môi trường thật của hệ điều hành vẫn được ưu tiên hơn file `.env`.

## Hai Agent

Bật `NIKO_AGENT_MODE=two_agent` để tách vai trò:

```text
Telegram gateway -> niko.graphs.chat_reply -> Niko Fast / Niko Deep -> niko.graphs.chat_reply -> Telegram gateway
```

Niko Fast là mặt giao tiếp nhanh và lớp triage cho các câu hỏi không chắc. Nếu Fast thấy có thể trả lời ngay, Fast trả lời trực tiếp. Nếu Fast thấy cần phân tích, cần tool/memory/tài liệu, hoặc không chắc, Niko Deep chạy background; Fast gửi câu báo đợi/báo bận trong lúc chờ.

Khi Niko Deep xử lý xong, kết quả nội bộ sẽ quay lại Niko Fast trước. Fast compose thành câu trả lời tự nhiên cho người dùng, rồi gateway mới gửi ra chat.

`NIKO_FAST_AGENT_COMMAND` nên trỏ tới model nhẹ/nhanh. Để trống thì hệ thống fallback về rule/template local cho wait/busy và gửi kết quả deep trực tiếp. Deep agent dùng `CLAUDE_DEEP_AGENT_COMMAND`; nếu để trống thì fallback về `CLAUDE_CLI_COMMAND`.

## Sửa Hook

Muốn đổi giọng văn thì sửa `niko/HOOK.md` và mở Pull Request. Không cần sửa script Python.

## PR

Tạo branch riêng, sửa hook/code, mở Pull Request vào `main`. `main` là nhánh chính, owner duyệt và merge.
