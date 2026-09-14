# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Bot Telegram nhỏ gọi Claude CLI (`fcc-claude`) để anh em trong group chat thử với Niko.

## Chạy Local

1. Tạo bot bằng [@BotFather](https://t.me/BotFather), lấy token.
2. Copy `.env.example` thành `.env` để điền cấu hình chung cho hệ thống.
3. Copy `bots/telegram/.env.example` thành `bots/telegram/.env` để điền cấu hình riêng của Telegram.

**Chú ý:** Cần cài Free Claude Code/FCC (đã cấu hình và điền API Key NVIDIA) và chạy `fcc-server` trước nếu `fcc-claude` đang trỏ qua FCC.

Root `.env`:

```env
CLAUDE_CLI_COMMAND=fcc-claude -p
CLAUDE_DEEP_AGENT_COMMAND=fcc-claude --bare --no-session-persistence --tools= -p
CLAUDE_WORKDIR=.runtime/claude_sandbox
CLAUDE_TIMEOUT_SECONDS=180
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A;telegram:987654321=Anh B
```

`bots/telegram/.env`:

```env
TELEGRAM_BOT_TOKEN=token_cua_bot
TELEGRAM_ALLOWED_CHAT_IDS=-100xxxxxxxxxx
TELEGRAM_GROUP_MODE=mentions
TELEGRAM_MENTION_REPLIES=1
TELEGRAM_AGENT_MODE=two_agent
TELEGRAM_PROMPT_HOOK_FILE=HOOK.md
TELEGRAM_REPLY_SUFFIX=Meow
TELEGRAM_STICKERS_ENABLED=1
```

4. Chạy bot:

```bash
python bots/telegram_bot.py
```

Khi chạy, bot sẽ load `.env` trước rồi load `bots/telegram/.env` sau. Biến trong `bots/telegram/.env` có thể override biến cùng tên đọc từ root `.env`, nhưng không override biến môi trường thật của hệ điều hành.

Dùng `/id` trong private chat hoặc `/id@TenBot` trong group để lấy `chat_id`. Nếu bot chạy trong group thì điền `chat_id` của group. Trong group, bot chỉ xử lý tin nhắn có tag tên bot. Khi trả lời, bot sẽ mention người vừa gọi nếu bật `TELEGRAM_MENTION_REPLIES=1`.

## Nhận Diện Người Chat

Dùng `/whoami` hoặc `/id` để lấy `user_key` của từng người. Bot sẽ gửi thông tin người nói vào prompt để Claude biết ai đang chat.

```env
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A;telegram:987654321=Anh B
```

`CHAT_ALLOWED_USER_KEYS` để trống thì ai trong chat được phép cũng dùng được bot. Nếu muốn giới hạn theo từng người, điền danh sách `user_key`, cách nhau bằng dấu phẩy.

## Hai Agent

Bật `TELEGRAM_AGENT_MODE=two_agent` để bot phản hồi ngay: câu đơn giản được rule local hoặc Agent nhanh xử lý, câu cần phân tích sẽ chạy deep agent ở background nên Telegram vẫn nhận tin mới trong lúc chờ Opus 5.

```env
TELEGRAM_AGENT_MODE=two_agent
TELEGRAM_FAST_AGENT_COMMAND=
TELEGRAM_FAST_AGENT_TIMEOUT_SECONDS=45
TELEGRAM_UNCERTAIN_DELAY_SECONDS=3
TELEGRAM_DEEP_WAIT_REPLY=Da anh doi em chut, cau nay can phan tich ky hon nen em day sang Opus 5 roi bao lai anh ngay.
TELEGRAM_DEEP_BUSY_REPLY=Da anh doi em chut, em van dang xu ly cau truoc. Anh cu nhan tiep, khi co ket qua em se gui lai.
```

Để `TELEGRAM_FAST_AGENT_COMMAND` trống thì Agent nhanh chỉ dùng rule local như chào hỏi, cảm ơn, ping, ok hoặc câu khen ngắn. Câu không rõ local/deep keyword sẽ chờ `TELEGRAM_UNCERTAIN_DELAY_SECONDS` giây rồi mới báo chờ và đẩy sang deep agent.

Deep agent dùng `CLAUDE_DEEP_AGENT_COMMAND` nếu có cấu hình, nếu không thì dùng `CLAUDE_CLI_COMMAND`. `--bare --no-session-persistence --tools=` giúp tắt tool/quét file của Claude CLI, còn `CLAUDE_WORKDIR` cho bot chạy CLI trong thư mục sandbox rỗng.

## Sửa Hook

Muốn đổi giọng văn thì sửa file `HOOK.md` rồi mở PR, không cần sửa script Python. Hook được nạp vào prompt mỗi lần bot gọi model.

```env
TELEGRAM_PROMPT_HOOK_FILE=HOOK.md
TELEGRAM_REPLY_SUFFIX=Meow
```

## Sticker Ducks

Bot có thể gửi thêm sticker Duck của Telegram (UtyaDuck) theo mood bằng local picker, không cần MCP.

```env
TELEGRAM_STICKERS_ENABLED=1
TELEGRAM_STICKER_CONFIG_FILE=stickers/ducks.json
TELEGRAM_STICKER_SET_NAME=UtyaDuck
TELEGRAM_STICKER_MODE=smart
```

Sửa keyword/emoji trong `stickers/ducks.json` nếu muốn đổi cách chọn sticker. `smart` chỉ gửi khi bắt được mood; đổi thành `always` nếu muốn câu nào cũng có sticker.

## PR

Tạo branch riêng, sửa hook/code, mở Pull Request vào `main`. `main` là nhánh chính và owner sẽ duyệt/merge.
