# Luồng Xử Lý Chat Telegram

Tài liệu này mô tả luồng xử lý tin nhắn Telegram hiện tại của Niko Agent. Gateway Telegram chỉ là cổng vào/ra; gateway gọi `GatewayRunner`, còn phần route, memory, trace và deep job hiện vẫn nằm trong `ChatReplyGraph`.

## Thành Phần

- `bots/telegram/bot.py`: Telegram gateway.
- `niko.chat_gateway`: chuẩn hóa message Telegram thành `ChatGatewayMessage`.
- `niko.gateway.GatewayRunner`: runner mỏng nhận message/callback từ gateway rồi ủy quyền sang chat graph hiện tại.
- `niko.graphs.chat_reply.graph.ChatReplyGraph`: điều phối flow chat.
- `niko.graphs.chat_reply.router`: rule router local/deep/fast/busy.
- `niko.graphs.chat_reply.prompts`: prompt task cho Fast Agent.
- `niko.runtime`: gọi Claude CLI cho Deep agent và inject memory context.
- `niko.harness.trace`: ghi JSONL trace.
- `niko.harness.runtime_log`: ghi runtime log cho tab Bots.
- `niko.memory`: lưu chat log, semantic facts, episodic events.
- `bots/telegram/instance_guard.py`: single-instance lock để dashboard/terminal không start trùng Telegram long polling.

## Gateway Telegram

`bots/telegram/bot.py` xử lý các việc liên quan Telegram:

1. Long polling bằng `getUpdates`.
2. Lấy `message` từ update.
3. Convert sang `ChatGatewayMessage`.
4. Nếu là `/id` hoặc `/whoami` đúng bot hiện tại thì trả identity ngay.
5. Nếu là group và `TELEGRAM_GROUP_MODE=mentions`, chỉ xử lý message có tag bot.
6. Kiểm tra `TELEGRAM_ALLOWED_CHAT_IDS` và `CHAT_ALLOWED_USER_KEYS`.
7. Tạo callback `deliver_reply` và `notify_working`.
8. Gọi `GATEWAY_RUNNER.handle_message(...)`.
9. Khi graph trả lời, gateway gửi message trước.
10. Nếu bật sticker, gateway chạy worker nền: hỏi Nimble local chọn mood sticker,
    rồi map mood đó sang file_id Telegram. Nếu Nimble chọn `no_sticker` hoặc lỗi,
    bot bỏ qua sticker.

Trước khi gọi Telegram API dài hạn, gateway lấy lock tại
`niko/.runtime/telegram_bot.lock`. Nếu đã có process khác giữ lock và PID còn
sống, bot mới thoát sớm với log `telegram_instance_conflict` thay vì để Telegram
báo `409 Conflict` ở `getUpdates`.

Gateway không quyết định dùng local/Fast/Deep. Nó cũng không retrieve memory.
Decision model trong sticker chỉ chọn mood trang trí sau reply, không ảnh hưởng
route chính của chat.

## Chế Độ Single Agent

Nếu `NIKO_AGENT_MODE=single`:

```text
Telegram -> GatewayRunner -> ChatReplyGraph -> Deep Agent -> Telegram
```

Graph gọi `runtime.call_deep_agent(...)` đồng bộ. Runtime nạp hook, identity context và memory context nếu bật.

## Chế Độ Two Agent

Nếu `NIKO_AGENT_MODE=two_agent`:

```text
Telegram message
  -> Telegram gateway
  -> GatewayRunner.handle_message
  -> ChatReplyGraph.handle_message
  -> router.decide_agent_route
  -> local reply | Fast triage | Deep background | busy reply
  -> Telegram reply
```

`conversation_id` ưu tiên `chat_id`, fallback về `user_key`. Mỗi conversation chỉ có một Deep job active tại một thời điểm.

## Route Hiện Tại

- `local_reply`: câu rất ngắn có thể trả lời bằng rule local, ví dụ chào, cảm ơn, ping.
- `fast_agent`: vùng xám khi có decision model; Nimble local quyết định `reply_now` hoặc `send_to_deep`.
- `deep_agent`: câu cần xử lý sâu, ví dụ có keyword `phân tích`, `thiết kế`, `debug`, `memory`, `fact`, `tool`, `github`, prompt dài, newline hoặc backtick.
- `busy_reply`: conversation đang có Deep job active.
- `delayed_deep_agent`: vùng xám nhưng không có triage model/Fast Agent; delay rồi đẩy Deep.

Trên dashboard:

- `local_reply` đi tuyến `Gateway -> Router -> Reply`.
- `fast_agent` với `reply_now` đi tuyến `Gateway -> Router -> Fast Agent -> Reply`.
- `deep_agent` đi tuyến `Gateway -> Router -> Memory Gate -> Loop/Deep -> Reply`.
- `busy_reply` đi tuyến `Gateway -> Router -> Reply`.

## Vai Trò Của Decision Model Và Fast Agent

Decision model dùng Ollama/Nimble qua `NIKO_DECISION_MODEL_*` để làm các quyết
định nhỏ, nhanh và có label đóng. Hiện tại nó đã làm triage local
`reply_now/send_to_deep` và chọn sticker mood. Hướng memory upgrade là dùng cùng
model này cho các decision point hẹp hơn trong chat memory: có cần retrieve
memory không, turn này có gì đáng nhớ không, memory ứng viên là semantic fact hay
episodic event, và user có đang yêu cầu sửa/quên memory không.

Trong luồng dashboard-first, warmup bằng tab `Bots -> Decision Model -> Warmup`;
action này chạy nền và dùng `NIKO_DECISION_MODEL_WARMUP_TIMEOUT_SECONDS` riêng để
tránh cắt request khi model đang cold-start. Nếu `NIKO_DECISION_MODEL_KEEP_ALIVE=-1`,
Ollama giữ model loaded cho tới khi bấm `Bots -> Decision Model -> Stop`, chạy
`ollama stop nimble`, hoặc restart Ollama.

Decision model không thay Deep agent và không tự ghi/sửa memory tùy ý. Nó chỉ trả
label/query/metadata để `ChatReplyGraph` hoặc memory pipeline quyết định bước kế
tiếp.

Fast Agent dùng `NIKO_FAST_AGENT_COMMAND` để sinh ngôn ngữ khi cần. Fast nên dùng model nhẹ/nhanh và có các task:

- `triage`: legacy fallback nếu decision model chưa bật.
- `reply`: trả lời cho local/fast reply nếu cần.
- `wait`: hiện không còn là logic chính cho wait; graph có fallback wait text.
- `busy`: báo Deep đang xử lý câu trước.
- `final`: compose output Deep thành câu trả lời tự nhiên.
- `error`: báo lỗi gọn nếu Deep lỗi.

Legacy Fast triage chỉ hợp lệ khi trả JSON:

```json
{"route":"reply_now","reply":"..."}
```

hoặc:

```json
{"route":"send_to_deep","reply":"Dạ anh đợi em chút, câu này cần thêm thời gian xử lý."}
```

Nếu Nimble hoặc legacy Fast triage lỗi, timeout, hoặc route không hợp lệ, graph fallback sang Deep.

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
- Nếu `NIKO_MEMORY_GATE_ENABLED=1`, Nimble local quyết định turn Deep này có cần
  search long-term memory không. Gate `skip` thì Deep không nhận facts/episodes;
  gate lỗi thì fail-open và retrieval chạy như cũ.

Retrieval hiện tại:

- Recent conversation được dựng từ `chat_log` theo `conversation_id` và inject
  như working memory ngắn hạn để Deep hiểu follow-up.
- Nếu hỏi kiểu “có fact nào”, store có thể list/search facts.
- Các câu thường dùng `search_facts` và `search_episodes`.
- Context được format thành các section `Recent conversation`, `Relevant semantic facts`
  và `Relevant episodic events`; current user message nằm ở section riêng cuối prompt.

`memory_retrieval_gate` đang default-off để không đổi hành vi demo hiện tại. Khi
bật trong dashboard Config, gate dùng Decision Model trước khi search store. Nếu
gate chọn `skip`, Deep không nhận memory context. Nếu gate chọn `retrieve`,
pipeline dùng query do gate đề xuất, hoặc raw prompt nếu model không trả query.
Nếu gate lỗi, retrieval fail-open bằng raw prompt để tránh bỏ lỡ memory thật sự
cần. Nếu gate chọn `skip`, recent conversation vẫn có thể được inject vì đó là
working memory ngắn hạn, không phải long-term retrieval.

Memory correction chạy trước local/fast/deep route thông thường khi
`NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=1`. Đây là Phase 5 V1 tạm thời: Nimble
chỉ chọn intent `none/correct_memory/forget_memory`, còn Python runtime search
facts, hỏi lại khi mơ hồ, validate `fact #...` trong pending choices và mới
update/delete SQLite có trace. Pending choices được lưu trong SQLite với TTL 15
phút để lượt chọn fact có thể sống qua restart runtime. Episode vẫn read-only qua
chat ở V1.

Jira issue flow chạy sau memory correction và trước local/fast/deep route thông
thường khi `NIKO_JIRA_TOOLS_ENABLED=1`. Nếu prompt có issue key dạng `NIKO-101`,
`ChatReplyGraph` gọi `niko/graphs/jira_issue/`, workflow dùng Loop tools để fetch
issue/comment/changelog từ fixture, format context có evidence và handoff sang
Deep. Nếu issue key không có trong fixture, bot trả reply an toàn và không gọi Deep.
Nếu prompt không có key rõ nhưng có tín hiệu Jira/task và
`NIKO_JIRA_DECISION_GATE_ENABLED=1`, Nimble chỉ chọn gate:
`use_jira_tool`, `ask_for_issue_key` hoặc `skip_jira`. Python vẫn parse/validate
issue key trước khi gọi tool; confidence thấp hoặc gate lỗi thì quay về route chat cũ.

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
- `memory_correction_decision`
- `memory_correction_clarify`
- `memory_correction_applied`
- `loop_started`
- `loop_tool_call_finished`
- `jira_issue_workflow_finished`
- `jira_gate_decision`
- `jira_gate_error`
- `deep_extra_context`
- `deep_agent_call_started`
- `deep_agent_call_finished`
- `wait_reply_delivered`
- `memory_write_chat_log`
- `memory_write_episode`
- `turn_end`
- `reply_delivery_error`

Dashboard đọc các event này để hiển thị live harness graph và trace tail. Các log vận hành thay cho terminal, ví dụ `telegram_message_processed`, `fast_triage_finished`, `sticker_decision`, được ghi vào `niko/.runtime/logs/YYYY-MM-DD.jsonl` và hiển thị trong tab Bots.

## Luồng Chi Tiết

```text
User Telegram message
  -> bot.py handle_message
     -> /id or /whoami?
        -> reply identity, stop
     -> group mention filter
     -> chat/user auth
     -> GatewayRunner.handle_message
        -> ChatReplyGraph.handle_message
        -> write turn_start + incoming chat log
        -> decide route
        -> memory correction intent?
           -> clarify/apply correction and reply, stop
        -> Jira issue key and NIKO_JIRA_TOOLS_ENABLED=1?
           -> Loop fetch issue/comment/changelog
           -> handoff Deep with Jira evidence context
        -> ambiguous Jira/task prompt and Jira decision gate enabled?
           -> ask issue key / skip Jira / use recent issue key and run Jira flow

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
- Semantic facts chủ yếu thêm thủ công hoặc từ explicit consolidation, gồm manual `Run once` và auto default-off.
- Episodic memory mới tóm tắt deep job.
- Retrieval còn dựa trên text search, chưa có embedding/rerank/Knowledge Graph.

## Test Liên Quan

```bash
python -m pytest
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
