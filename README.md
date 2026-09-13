# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Bot Telegram nhỏ gọi Claude CLI (`fcc-claude`) để anh em trong group chat thử với Niko.

## Chạy local

1. Tạo bot bằng [@BotFather](https://t.me/BotFather), lấy token.
2. Copy `.env.example` thành `.env`, điền:

**Chú ý:** Cần cài Free Claude Code/FCC (đã cấu hình và điền API Key NVIDIA) và chạy `fcc-server` trước nếu `fcc-claude` đang trỏ qua FCC.

```env
TELEGRAM_BOT_TOKEN=token_cua_bot
TELEGRAM_ALLOWED_CHAT_IDS=-100xxxxxxxxxx
CLAUDE_CLI_COMMAND=fcc-claude -p
```

3. Chạy bot:

```bash
python bots/telegram_bot.py
```

Dùng `/id` trong private chat hoặc group để lấy `chat_id`. Nếu bot chạy trong group thì điền `chat_id` của group.

## Giữ phiên Claude

Bot có thể tiếp tục phiên gần nhất nếu context chưa tới 80%:

```env
CLAUDE_SESSION_MODE=auto_resume
CLAUDE_RESUME_COMMAND=fcc-claude --continue -p
CLAUDE_CONTEXT_USAGE_COMMAND=python scripts/claude_context_usage.py
CLAUDE_CONTEXT_LIMIT_PERCENT=80
CLAUDE_NEW_SESSION_COMMAND=fcc-resume
```

Khi context chạm ngưỡng, bot sẽ báo lên chat rồi mở phiên mới.

## Sửa hook

Muốn đổi giọng văn thì sửa biến này trong `.env.example` hoặc mở PR:

```env
TELEGRAM_PROMPT_HOOK=...
TELEGRAM_REPLY_SUFFIX=Ok nhé bạn
```

## Demo file prompt

```bash
python main.py
```

Script đọc `examples/input.txt`, gọi `fcc-claude`, rồi ghi kết quả vào `output.txt`.

## PR

Tạo branch riêng, sửa hook/code, mở Pull Request vào `main`. `main` là nhánh chính và owner sẽ duyệt/merge.
