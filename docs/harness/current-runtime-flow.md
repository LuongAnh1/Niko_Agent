# Luồng Runtime Hiện Tại

Ngày cập nhật: 2026-10-08

Tài liệu này mô tả luồng đang chạy trong repo, không phải roadmap. Nên đọc sau
`docs/harness/architecture.md` và trước các tài liệu thành phần như Telegram,
memory, Loop hoặc Jira.

## Bản Đồ Một Lượt Chat

```text
Telegram update
  -> bots/telegram/bot.py
     - lọc allowlist, mention, command /id /whoami
     - chuẩn hóa message
  -> ChatGatewayMessage
  -> GatewayRunner
     - handoff channel-agnostic
  -> NikoApp
     - mở turn, trace, chat log
     - kiểm tra busy state
     - chọn memory correction nếu prompt là sửa/xóa/quên fact
     - chọn Jira issue workflow nếu Jira tools được bật và prompt hợp lệ
     - còn lại chuyển sang ChatReplyGraph
  -> Reply
     - gửi text, optional sticker
     - ghi trace/runtime log
```

`GatewayRunner` chỉ là cầu nối gateway-agnostic. Quyền chọn workflow cấp turn
hiện nằm ở `NikoApp`, theo tinh thần Waku assembly root.

## Nhánh Chat Bình Thường

```text
NikoApp
  -> ChatReplyGraph
     -> local rule
        -> reply ngay cho greeting/thanks/ping rất rõ
     -> fast triage
        -> Nimble hoặc fallback triage, có recent chat window ngắn
        -> reply_now gọi Fast Agent nếu cần text
        -> send_to_deep tạo deep background job
     -> deep path
        -> MemoryRuntime retrieval
        -> Claude CLI runtime
        -> optional Fast final compose
        -> ghi episode/chat_log/trace
```

Fast triage và Fast reply có thể nhận recent chat window để hiểu follow-up gần,
nhưng không tự search long-term facts/episodes. Long-term memory retrieval vẫn là
trách nhiệm của Deep path qua `MemoryRuntime`.

## Nhánh Memory

```text
chat_log
  -> working memory window
     -> Deep prompt, correction decision context khi có tín hiệu rõ

facts / episodes
  -> retrieval gate
     -> search/list/recent/none
     -> formatted context cho Deep

correction prompt
  -> MemoryCorrectionWorkflow
     -> tìm fact khớp
     -> hỏi lại nếu mơ hồ
     -> durable pending fact choices trong SQLite
     -> update/delete qua guardrail Python

consolidation
  -> dashboard Refresh batch / Run once
  -> optional auto consolidation default-off sau đủ complete exchanges
```

`chat_log` là log vận hành, không phải Semantic Memory. `facts` là semantic
baseline, `episodes` là episodic baseline. Working memory chỉ là cửa sổ tạm thời
được dựng lại từ `chat_log`.

## Nhánh Jira Runtime Tools

```text
NikoApp
  -> issue-key rule hoặc Jira Decision Gate
  -> JiraIssueAnalysisWorkflow
     -> LoopRuntime
        -> parse_issue_key
        -> fetch_jira_issue
        -> fetch_jira_comments
        -> fetch_jira_changelog
     -> format evidence context
     -> Deep Agent
     -> Reply
```

Jira tools hiện là read-only fixture tools để chứng minh tool lane. Chúng không
phải Jira bot/gateway thật và không phải tầng lakehouse/KG. Prompt có issue key
rõ đi qua rule Python; prompt mơ hồ chỉ hỏi Nimble khi `NIKO_JIRA_DECISION_GATE_ENABLED=1`.
Confidence thấp hoặc thiếu issue key hợp lệ không mở tool.

## Ranh Giới Cần Giữ

- `bots/telegram/`: chỉ platform IO, auth, parsing, send/reply và sticker.
- `niko/gateway/`: chỉ handoff message/callback đã chuẩn hóa vào app.
- `niko/app.py`: assembly root, chọn workflow cấp turn, không sinh reply tự do.
- `niko/graphs/chat_reply/`: luồng local/Fast/Deep/busy cho chat bình thường.
- `niko/graphs/jira_issue/`: workflow phân tích Jira issue có evidence.
- `niko/loop/`: runtime tool-loop dùng chung, không biết Telegram/Jira/memory policy.
- `niko/tools/<domain>/`: adapter tool theo domain, không gửi platform reply.
- `niko/memory/`: store/runtime/correction/consolidation cho chat memory local.
- `niko/harness/` và `niko/ops/`: quan sát, log, dashboard và vận hành.

Nếu sau này cần thêm workflow mới, ưu tiên tạo graph/workflow thật dưới
`niko/graphs/` hoặc tool adapter dưới `niko/tools/<domain>/`. Không tạo thêm một
lớp cầu nối chỉ để forward call.
