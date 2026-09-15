# Niko Agent Context

Day la project Python cho Niko Agent. Niko dung Claude CLI (`fcc-claude`) thay cho viec goi API LLM truc tiep.

## Current Architecture

- `bots/telegram/` la gateway Telegram: polling, parse message, auth, mention filter, `/id`, send reply, send sticker.
- `niko/graphs/chat_reply/` la graph nghiep vu chat: route tin nhan, goi Niko Fast, day Niko Deep, quan ly deep job background, compose final reply.
- `niko/runtime.py` la runtime goi Claude CLI: doc env command, nap `niko/HOOK.md`, chen identity context, resolve `CLAUDE_WORKDIR`.
- `niko/chat_gateway.py` chuan hoa identity/message chung cho cac cong chat.
- `stickers/ducks.json` la local sticker picker config cho Telegram Duck.

## Important Docs

- `docs/architecture.md`: tong quan bo cuc repo va ranh gioi module.
- `docs/telegram-chat-flow.md`: luong xu ly tin nhan Telegram voi hai agent.
- `README.md`: huong dan chay local va env ngan gon.

## Current Chat Flow

Telegram gateway khong tu quyet dinh LLM. Gateway convert message ve `ChatGatewayMessage`, sau do goi:

```python
from niko.graphs.chat_reply import ChatReplyGraph
```

`ChatReplyGraph` xu ly `single` hoac `two_agent` theo `NIKO_AGENT_MODE`.

Trong `two_agent`:

- Local rule tra loi nhanh cho greeting/thanks/ping/praise.
- Keyword/prompt dai/newline/backtick di Deep.
- Gray zone di Niko Fast triage neu co `NIKO_FAST_AGENT_COMMAND`.
- Fast `reply_now` tra loi ngay.
- Fast `send_to_deep` tao Deep background job va gui wait reply.
- Khi Deep xong, output noi bo quay lai Fast task `final` de compose cau tra loi cuoi.
- Neu Deep dang ban trong cung conversation, tin moi duoc them vao `DeepAgentJob.followups` va bot tra `busy_reply`.

## Runtime Notes

- `CLAUDE_WORKDIR` mac dinh nen tro vao `niko/.runtime/claude_sandbox` de Claude CLI khong tu nhin thang vao toan repo.
- Hien chua co Working Memory, RAG, Tool Router hay Memory Layer rieng.
- `HOOK.md` duoc nap cho Deep va cac task Fast reply/wait/busy/final/error. Fast triage JSON khong nap hook.
- Khong tao lai `niko/agent.py` hay `niko/agent_router.py`; graph hien nam trong `niko/graphs/chat_reply/`.

## Commands

Dung `rtk` cho shell commands trong workspace nay.

```bash
rtk python -X utf8 -m unittest discover
rtk python -m bots.telegram.bot
```
