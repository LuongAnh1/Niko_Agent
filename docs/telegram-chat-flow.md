# Luồng Xử Lý Chat Telegram

Tài liệu này mô tả luồng xử lý tin nhắn Telegram hiện tại của Niko Agent. Kiến trúc đang chạy theo hướng hai agent: Niko Fast để phản hồi nhanh/triage, Niko Deep để xử lý việc cần suy nghĩ kỹ hơn.

## Thành Phần

- `bots/telegram/bot.py`: Telegram gateway.
- `niko.chat_gateway`: chuẩn hóa message Telegram thành `ChatGatewayMessage`.
- `niko.graphs.chat_reply.graph.ChatReplyGraph`: điều phối flow chat.
- `niko.graphs.chat_reply.router`: rule router local/deep/fast/busy.
- `niko.graphs.chat_reply.prompts`: prompt task cho Niko Fast.
- `niko.runtime`: gọi Claude CLI cho Fast/Deep thông qua env command.

## Luồng Gateway Telegram

`bots/telegram/bot.py` chỉ làm cổng vào/ra:

1. Long polling Telegram bằng `getUpdates`.
2. Lấy `message` từ update.
3. Convert sang `ChatGatewayMessage` bằng `telegram_message_to_gateway`.
4. Nếu là `/id` hoặc `/whoami` đúng cho bot hiện tại thì trả identity ngay, không cần tag bot trong group.
5. Nếu là group và `TELEGRAM_GROUP_MODE=mentions`, chỉ xử lý message có tag `@TenBot`.
6. Kiểm tra `TELEGRAM_ALLOWED_CHAT_IDS` và `CHAT_ALLOWED_USER_KEYS`.
7. Tạo callback `deliver_reply` và `notify_working`.
8. Gọi `CHAT_REPLY_GRAPH.handle_message(prompt, prompt_message, deliver_reply, notify_working)`.
9. Khi graph trả lời, gateway gửi message, mention người gọi nếu bật `TELEGRAM_MENTION_REPLIES=1`, và có thể gửi sticker Duck nếu bật sticker.

Gateway không quyết định dùng Fast hay Deep. Quyết định đó nằm trong `ChatReplyGraph`.

## Chế Độ Single Agent

Nếu `NIKO_AGENT_MODE=single`, flow rất ngắn:

```text
Telegram -> ChatReplyGraph -> Niko Deep -> Telegram
```

Graph gọi `runtime.call_deep_agent(...)` đồng bộ, gắn suffix bằng `NIKO_REPLY_SUFFIX`, rồi callback về Telegram.

## Chế Độ Two Agent

Nếu `NIKO_AGENT_MODE=two_agent`, flow hiện tại:

```text
Telegram message
  -> Telegram gateway
  -> ChatReplyGraph.handle_message
  -> router.decide_agent_route
  -> local reply | Fast triage | Deep background | busy reply
  -> Telegram reply
```

`ChatReplyGraph` tính `conversation_id` theo `chat_id` nếu có, fallback về `user_key`. Mỗi conversation chỉ có một Deep job đang chạy tại một thời điểm.

## Route Hiện Tại

Router trả về một trong các route sau:

- `busy_reply`: đang có Deep job active trong cùng conversation.
- `local_reply`: tin ngắn có thể trả lời bằng rule local, ví dụ chào, cảm ơn, ping, praise.
- `deep_agent`: prompt có keyword/format cần xử lý sâu, ví dụ `phân tích`, `thiết kế`, `debug`, `viết code`, `memory`, `tool`, `mcp`, `telegram`, `github`, prompt dài, có newline hoặc backtick.
- `fast_agent`: vùng xám khi có `NIKO_FAST_AGENT_COMMAND`; Fast Agent triage xem trả lời ngay hay đẩy Deep.
- `delayed_deep_agent`: vùng xám nhưng không có Fast Agent; đợi `NIKO_UNCERTAIN_DELAY_SECONDS` rồi đẩy Deep.

## Vai Trò Của Niko Fast

Niko Fast dùng command trong `NIKO_FAST_AGENT_COMMAND`, nên nên chọn model nhẹ và nhanh. Fast có các task:

- `triage`: phân loại JSON, không nạp `HOOK.md`, không thêm `Meow`.
- `reply`: trả lời trực tiếp cho local route nếu có Fast command.
- `wait`: báo người dùng đợi khi Deep vừa bắt đầu.
- `busy`: báo người dùng Deep vẫn đang xử lý câu trước.
- `final`: biến output nội bộ của Deep thành câu trả lời tự nhiên cho người dùng.
- `error`: báo lỗi gọn nếu Deep lỗi.

Fast triage chỉ hợp lệ khi trả JSON:

```json
{"route":"reply_now","reply":"..."}
```

hoặc:

```json
{"route":"send_to_deep","reply":"Dạ anh đợi em chút, câu này em chuyển Niko Deep xử lý rồi báo lại anh ngay."}
```

Nếu Fast triage lỗi JSON hoặc route không hợp lệ, graph fallback sang Deep.

## Vai Trò Của Niko Deep

Niko Deep dùng `CLAUDE_DEEP_AGENT_COMMAND`. Nếu biến này để trống, runtime fallback về `CLAUDE_CLI_COMMAND`.

Deep chạy background thread khi route cần xử lý sâu. Trong lúc Deep chạy:

- Bot đã gửi wait reply cho người dùng.
- Nếu người dùng nhắn thêm trong cùng conversation, graph trả `busy_reply`.
- Tin nhắn thêm sẽ được lưu vào `DeepAgentJob.followups`.
- Khi Deep xong, task `final` của Fast sẽ nhận câu hỏi gốc, output của Deep, và các followup gần nhất để compose câu trả lời cuối.

## Hook Và Prompt

`niko/HOOK.md` được nạp qua `NIKO_PROMPT_HOOK_FILE`.

Hiện tại hook được nạp cho:

- Deep Agent.
- Fast task `reply`.
- Fast task `wait`.
- Fast task `busy`.
- Fast task `final`.
- Fast task `error`.

Hook không nạp cho Fast task `triage`, vì triage cần JSON sạch.

Identity context được chèn nếu `CHAT_IDENTITY_ENABLED=1`. Context này chỉ nói bot biết người đang chat là ai, không phải memory dài hạn.

## Luồng Chi Tiết

```text
User Telegram message
  -> bot.py handle_message
     -> /id or /whoami?
        -> reply identity, stop
     -> group mention filter
     -> chat/user auth
     -> ChatReplyGraph.handle_message

ChatReplyGraph
  -> single mode?
     -> Deep sync -> reply
  -> two_agent mode
     -> active Deep job?
        -> busy_reply -> Fast busy or fallback busy text
     -> local rule?
        -> local_reply -> Fast reply or fallback local text
     -> deep keyword?
        -> deep_agent -> start Deep background -> Fast wait or fallback wait text
     -> Fast available?
        -> fast_agent -> Fast triage
           -> reply_now -> reply immediately
           -> send_to_deep -> start Deep background -> wait reply
           -> invalid -> fallback Deep background
     -> no Fast
        -> delayed_deep_agent -> delay -> Deep background

Deep background
  -> runtime.call_deep_agent
  -> prompts.compose_deep_answer_for_user
     -> Fast final if available
     -> fallback raw Deep answer
  -> deliver reply through Telegram gateway
```

## Giới Hạn Hiện Tại

- Chưa có Working Memory riêng.
- Chưa có RAG/tài liệu cố định.
- Chưa có Tool Router.
- Chưa có busy triage: khi Deep đang chạy, tin mới trong cùng conversation hiện được xem là followup và trả busy reply.
- Claude CLI chạy trong `CLAUDE_WORKDIR`, hiện là `niko/.runtime/claude_sandbox`, để hạn chế việc CLI tự nhìn thẳng vào toàn bộ repo.

## Test Liên Quan

Chạy:

```bash
rtk python -X utf8 -m unittest discover
```

Scenario chính đang được test:

- Sticker/reply trong group không tag bot thì bị bỏ qua.
- `/id` trong group không cần tag vẫn trả identity.
- Keyword deep đi Deep.
- Vùng xám gọi Fast triage.
- Fast `reply_now` không mở Deep.
- Fast `send_to_deep` mở Deep background và gửi wait reply.
- Deep xong đi qua Fast final compose.
- Deep đang bận thì đi `busy_reply`.
