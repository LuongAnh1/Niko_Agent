# Luồng Xử Lý Chat Telegram

Tài liệu này mô tả luồng xử lý tin nhắn Telegram hiện tại của Niko Agent. Gateway Telegram chỉ là cổng vào/ra; phần route, memory, trace và deep job nằm trong `ChatReplyGraph`.

## Thành Phần

- `bots/telegram/bot.py`: Telegram gateway.
- `niko.chat_gateway`: chuẩn hóa message Telegram thành `ChatGatewayMessage`.
- `niko.graphs.chat_reply.graph.ChatReplyGraph`: điều phối flow chat.
- `niko.graphs.chat_reply.router`: rule router local/deep/fast/busy.
- `niko.graphs.chat_reply.prompts`: prompt task cho Fast Agent.
- `niko.runtime`: gọi Claude CLI cho Deep agent và inject memory context.
- `niko.harness.trace`: ghi JSONL trace.
- `niko.memory`: lưu chat log, semantic facts, episodic events.

## Gateway Telegram

`bots/telegram/bot.py` xử lý các việc liên quan Telegram:

1. Long polling bằng `getUpdates`.
2. Lấy `message` từ update.
3. Convert sang `ChatGatewayMessage`.
4. Nếu là `/id` hoặc `/whoami` đúng bot hiện tại thì trả identity ngay.
5. Nếu là group và `TELEGRAM_GROUP_MODE=mentions`, chỉ xử lý message có tag bot.
6. Kiểm tra `TELEGRAM_ALLOWED_CHAT_IDS` và `CHAT_ALLOWED_USER_KEYS`.
7. Tạo callback `deliver_reply` và `notify_working`.
8. Gọi `CHAT_REPLY_GRAPH.handle_message(...)`.
9. Khi graph trả lời, gateway gửi message và có thể gửi sticker nền nếu bật sticker.

Gateway không quyết định dùng local/Fast/Deep. Nó cũng không retrieve memory.

## Chế Độ Single Agent

Nếu `NIKO_AGENT_MODE=single`:

```text
Telegram -> ChatReplyGraph -> Deep Agent -> Telegram
```

Graph gọi `runtime.call_deep_agent(...)` đồng bộ. Runtime nạp hook, identity context và memory context nếu bật.

## Chế Độ Two Agent

Nếu `NIKO_AGENT_MODE=two_agent`:

```text
Telegram message
  -> Telegram gateway
  -> ChatReplyGraph.handle_message
  -> router.decide_agent_route
  -> local reply | Fast triage | Deep background | busy reply
  -> Telegram reply
```

`conversation_id` ưu tiên `chat_id`, fallback về `user_key`. Mỗi conversation chỉ có một Deep job active tại một thời điểm.

## Route Hiện Tại

- `local_reply`: câu rất ngắn có thể trả lời bằng rule local, ví dụ chào, cảm ơn, ping.
- `fast_agent`: vùng xám khi có Fast Agent; Fast quyết định `reply_now` hoặc `send_to_deep`.
- `deep_agent`: câu cần xử lý sâu, ví dụ có keyword `phân tích`, `thiết kế`, `debug`, `memory`, `fact`, `tool`, `github`, prompt dài, newline hoặc backtick.
- `busy_reply`: conversation đang có Deep job active.
- `delayed_deep_agent`: vùng xám nhưng không có Fast Agent; delay rồi đẩy Deep.

Trên dashboard:

- `local_reply` đi tuyến `Gateway -> Router -> Reply`.
- `fast_agent` với `reply_now` đi tuyến `Gateway -> Router -> Fast Agent -> Reply`.
- `deep_agent` đi tuyến `Gateway -> Router -> Memory Gate -> Loop/Deep -> Reply`.
- `busy_reply` đi tuyến `Gateway -> Router -> Reply`.

## Vai Trò Của Fast Agent

Fast Agent dùng `NIKO_FAST_AGENT_COMMAND`. Fast nên dùng model nhẹ/nhanh và có các task:

- `triage`: phân loại JSON, không nạp hook, không thêm suffix.
- `reply`: trả lời cho local/fast reply nếu cần.
- `wait`: hiện không còn là logic chính cho wait; graph có fallback wait text.
- `busy`: báo Deep đang xử lý câu trước.
- `final`: compose output Deep thành câu trả lời tự nhiên.
- `error`: báo lỗi gọn nếu Deep lỗi.

Fast triage chỉ hợp lệ khi trả JSON:

```json
{"route":"reply_now","reply":"..."}
```

hoặc:

```json
{"route":"send_to_deep","reply":"Dạ anh đợi em chút, câu này cần thêm thời gian xử lý."}
```

Nếu Fast triage lỗi JSON hoặc route không hợp lệ, graph fallback sang Deep.

## Vai Trò Của Deep Agent

Deep Agent dùng `CLAUDE_DEEP_AGENT_COMMAND`. Nếu biến này để trống, runtime fallback về `CLAUDE_CLI_COMMAND`.

Deep chạy background thread khi route cần xử lý sâu:

1. Graph reserve deep job.
2. Graph gửi wait reply trước rồi mới start thread để tránh đảo thứ tự Telegram.
3. Runtime retrieve memory context.
4. Runtime build prompt với hook, identity context và memory context.
5. Runtime gọi `fcc-claude`.
6. Kết quả Deep được đưa qua Fast `final` nếu có Fast command.
7. Graph gửi final reply.
8. Graph ghi `chat_log`, `episodes`, trace events.

Nếu người dùng nhắn thêm khi Deep đang chạy, graph trả `busy_reply` và lưu followup vào `DeepAgentJob.followups`.

## Memory Trong Flow

Fast triage không nhận memory context để tránh làm hỏng JSON.

Deep agent nhận memory context khi:

- `NIKO_MEMORY_ENABLED=1`
- `NIKO_MEMORY_RETRIEVAL_ENABLED=1`

Retrieval hiện tại:

- Nếu hỏi kiểu “có fact nào”, store có thể list/search facts.
- Các câu thường dùng `search_facts` và `search_episodes`.
- Context được format thành block `Semantic memory / facts` và `Episodic memory / events`.

Lưu ý: `chat_log` là log hội thoại, không đồng nghĩa với Semantic/Episodic Memory dùng để suy luận. Dashboard vì vậy không coi `memory_write_chat_log` là đường đi qua `Memory Records`.

## Trace Events Chính

Mỗi turn có thể có các event:

- `turn_start`
- `route_decision`
- `fast_triage_started`
- `fast_triage_finished`
- `deep_job_queued`
- `deep_job_started`
- `memory_retrieval`
- `deep_agent_call_started`
- `deep_agent_call_finished`
- `wait_reply_delivered`
- `memory_write_chat_log`
- `memory_write_episode`
- `turn_end`
- `reply_delivery_error`

Dashboard đọc các event này để hiển thị live harness graph và trace tail.

## Luồng Chi Tiết

```text
User Telegram message
  -> bot.py handle_message
     -> /id or /whoami?
        -> reply identity, stop
     -> group mention filter
     -> chat/user auth
     -> ChatReplyGraph.handle_message
        -> write turn_start + incoming chat log
        -> decide route

Route:
  local_reply
    -> reply immediately

  fast_agent
    -> Fast triage
       -> reply_now
          -> reply immediately
       -> send_to_deep
          -> wait reply
          -> Deep background

  deep_agent / delayed_deep_agent
    -> wait reply
    -> Deep background

  busy_reply
    -> add followup
    -> reply busy

Deep background:
  -> retrieve memory context
  -> call fcc-claude
  -> Fast final compose if available
  -> final reply
  -> write episode
  -> turn_end
```

## Giới Hạn Hiện Tại

- Tool router chưa hoàn chỉnh.
- Loop mới là khung/slot trên dashboard, chưa phải multi-step planner thực thụ.
- Semantic facts chủ yếu thêm thủ công.
- Episodic memory mới tóm tắt deep job.
- Retrieval còn dựa trên text search, chưa có embedding/rerank/Knowledge Graph.

## Test Liên Quan

```bash
python -m unittest discover
```

Scenario chính đang được test:

- Group message không tag bot thì bị bỏ qua.
- `/id` trong group không cần tag vẫn trả identity.
- Keyword deep đi Deep.
- Vùng xám gọi Fast triage.
- Fast `reply_now` không mở Deep.
- Fast `send_to_deep` mở Deep background và gửi wait reply.
- Wait reply được gửi trước khi thread Deep start.
- Deep xong đi qua Fast final compose.
- Deep đang bận thì đi `busy_reply`.
- Deep prompt có thể nhận memory context từ SQLite.
- Deep job hoàn tất ghi chat log, episodic memory và trace JSONL.
