# Telegram Chat Flow

Tai lieu nay mo ta luong xu ly tin nhan Telegram hien tai cua Niko Agent. Kien truc dang chay theo huong hai agent: Niko Fast de phan hoi nhanh/triage, Niko Deep de xu ly viec can suy nghi ky hon.

## Thanh Phan

- `bots/telegram/bot.py`: Telegram gateway.
- `niko.chat_gateway`: chuan hoa message Telegram thanh `ChatGatewayMessage`.
- `niko.graphs.chat_reply.graph.ChatReplyGraph`: dieu phoi flow chat.
- `niko.graphs.chat_reply.router`: rule router local/deep/fast/busy.
- `niko.graphs.chat_reply.prompts`: prompt task cho Niko Fast.
- `niko.runtime`: goi Claude CLI cho Fast/Deep thong qua env command.

## Luong Gateway Telegram

`bots/telegram/bot.py` chi lam cong vao/ra:

1. Long polling Telegram bang `getUpdates`.
2. Lay `message` tu update.
3. Convert sang `ChatGatewayMessage` bang `telegram_message_to_gateway`.
4. Neu la `/id` hoac `/whoami` dung cho bot hien tai thi tra identity ngay, khong can tag bot trong group.
5. Neu la group va `TELEGRAM_GROUP_MODE=mentions`, chi xu ly message co tag `@TenBot`.
6. Kiem tra `TELEGRAM_ALLOWED_CHAT_IDS` va `CHAT_ALLOWED_USER_KEYS`.
7. Tao callback `deliver_reply` va `notify_working`.
8. Goi `CHAT_REPLY_GRAPH.handle_message(prompt, prompt_message, deliver_reply, notify_working)`.
9. Khi graph tra loi, gateway gui message, mention nguoi goi neu bat `TELEGRAM_MENTION_REPLIES=1`, va co the gui sticker Duck neu bat sticker.

Gateway khong quyet dinh dung Fast hay Deep. Quyet dinh do nam trong `ChatReplyGraph`.

## Che Do Single Agent

Neu `NIKO_AGENT_MODE=single`, flow rat ngan:

```text
Telegram -> ChatReplyGraph -> Niko Deep -> Telegram
```

Graph goi `runtime.call_deep_agent(...)` dong bo, gan suffix bang `NIKO_REPLY_SUFFIX`, roi callback ve Telegram.

## Che Do Two Agent

Neu `NIKO_AGENT_MODE=two_agent`, flow hien tai:

```text
Telegram message
  -> Telegram gateway
  -> ChatReplyGraph.handle_message
  -> router.decide_agent_route
  -> local reply | Fast triage | Deep background | busy reply
  -> Telegram reply
```

`ChatReplyGraph` tinh `conversation_id` theo `chat_id` neu co, fallback ve `user_key`. Moi conversation chi co mot Deep job dang chay tai mot thoi diem.

## Route Hien Tai

Router tra ve mot trong cac route sau:

- `busy_reply`: dang co Deep job active trong cung conversation.
- `local_reply`: tin ngan co the tra loi bang rule local, vi du chao, cam on, ping, praise.
- `deep_agent`: prompt co keyword/format can xu ly sau, vi du `phan tich`, `thiet ke`, `debug`, `viet code`, `memory`, `tool`, `mcp`, `telegram`, `github`, prompt dai, co newline hoac backtick.
- `fast_agent`: vung xam khi co `NIKO_FAST_AGENT_COMMAND`; Fast Agent triage xem tra loi ngay hay day Deep.
- `delayed_deep_agent`: vung xam nhung khong co Fast Agent; doi `NIKO_UNCERTAIN_DELAY_SECONDS` roi day Deep.

## Vai Tro Cua Niko Fast

Niko Fast dung command trong `NIKO_FAST_AGENT_COMMAND`, nen nen chon model nhe va nhanh. Fast co cac task:

- `triage`: phan loai JSON, khong nap `HOOK.md`, khong them `Meow`.
- `reply`: tra loi truc tiep cho local route neu co Fast command.
- `wait`: bao nguoi dung doi khi Deep vua bat dau.
- `busy`: bao nguoi dung Deep van dang xu ly cau truoc.
- `final`: bien output noi bo cua Deep thanh cau tra loi tu nhien cho nguoi dung.
- `error`: bao loi gon neu Deep loi.

Fast triage chi hop le khi tra JSON:

```json
{"route":"reply_now","reply":"..."}
```

hoac:

```json
{"route":"send_to_deep","reply":"Da anh doi em chut, cau nay em chuyen Niko Deep xu ly roi bao lai anh ngay."}
```

Neu Fast triage loi JSON hoac route khong hop le, graph fallback sang Deep.

## Vai Tro Cua Niko Deep

Niko Deep dung `CLAUDE_DEEP_AGENT_COMMAND`. Neu bien nay de trong, runtime fallback ve `CLAUDE_CLI_COMMAND`.

Deep chay background thread khi route can xu ly sau. Trong luc Deep chay:

- Bot da gui wait reply cho nguoi dung.
- Neu nguoi dung nhan them trong cung conversation, graph tra `busy_reply`.
- Tin nhan them se duoc luu vao `DeepAgentJob.followups`.
- Khi Deep xong, task `final` cua Fast se nhan cau hoi goc, output cua Deep, va cac followup gan nhat de compose cau tra loi cuoi.

## Hook Va Prompt

`niko/HOOK.md` duoc nap qua `NIKO_PROMPT_HOOK_FILE`.

Hien tai hook duoc nap cho:

- Deep Agent.
- Fast task `reply`.
- Fast task `wait`.
- Fast task `busy`.
- Fast task `final`.
- Fast task `error`.

Hook khong nap cho Fast task `triage`, vi triage can JSON sach.

Identity context duoc chen neu `CHAT_IDENTITY_ENABLED=1`. Context nay chi noi bot biet nguoi dang chat la ai, khong phai memory dai han.

## Luong Chi Tiet

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

## Gioi Han Hien Tai

- Chua co Working Memory rieng.
- Chua co RAG/tai lieu co dinh.
- Chua co Tool Router.
- Chua co busy triage: khi Deep dang chay, tin moi trong cung conversation hien duoc xem la followup va tra busy reply.
- Claude CLI chay trong `CLAUDE_WORKDIR`, hien la `niko/.runtime/claude_sandbox`, de han che viec CLI tu nhin thang vao toan bo repo.

## Test Lien Quan

Chay:

```bash
rtk python -X utf8 -m unittest discover
```

Scenario chinh dang duoc test:

- Sticker/reply trong group khong tag bot thi bi bo qua.
- `/id` trong group khong can tag van tra identity.
- Keyword deep di Deep.
- Vung xam goi Fast triage.
- Fast `reply_now` khong mo Deep.
- Fast `send_to_deep` mo Deep background va gui wait reply.
- Deep xong di qua Fast final compose.
- Deep dang ban thi di `busy_reply`.
