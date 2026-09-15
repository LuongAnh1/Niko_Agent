# Niko Agent Context

This is a Python project for Niko Agent. Niko uses Claude CLI (`fcc-claude`) instead of calling an LLM API directly.

## Current Architecture

- `bots/telegram/` is the Telegram gateway: polling, message parsing, auth, mention filtering, `/id`, replies, and stickers.
- `niko/graphs/chat_reply/` is the chat business graph: route messages, call Niko Fast, hand off to Niko Deep, manage background deep jobs, and compose final replies.
- `niko/runtime.py` is the Claude CLI runtime: read command env, load `niko/HOOK.md`, inject identity context, and resolve `CLAUDE_WORKDIR`.
- `niko/chat_gateway.py` normalizes cross-channel identity and message data.
- `bots/telegram/stickers/ducks.json` is the local Telegram Duck sticker picker config.

## Important Docs

- `docs/architecture.md`: repo layout and module boundaries.
- `docs/telegram-chat-flow.md`: Telegram message flow with the two-agent design.
- `README.md`: short local setup and env guide.

## Current Chat Flow

The Telegram gateway does not decide which LLM path to use. It converts incoming Telegram messages into `ChatGatewayMessage`, then calls:

```python
from niko.graphs.chat_reply import ChatReplyGraph
```

`ChatReplyGraph` runs either `single` or `two_agent` mode according to `NIKO_AGENT_MODE`.

In `two_agent` mode:

- Local rules answer simple greeting/thanks/ping/praise messages quickly.
- Deep keywords, long prompts, newlines, or backticks go to Niko Deep.
- Gray-zone prompts go to Niko Fast triage when `NIKO_FAST_AGENT_COMMAND` is configured.
- Fast `reply_now` replies immediately.
- Fast `send_to_deep` starts a Deep background job and sends a wait reply.
- When Deep finishes, its internal output goes back through the Fast `final` task to compose the final user-facing reply.
- If Deep is already busy in the same conversation, new messages are appended to `DeepAgentJob.followups` and the bot returns `busy_reply`.

## Runtime Notes

- `CLAUDE_WORKDIR` should default to `niko/.runtime/claude_sandbox` so Claude CLI does not inspect the whole repo by default.
- The project does not yet have a dedicated Working Memory, RAG layer, Tool Router, or Memory Layer.
- `HOOK.md` is loaded for Deep and Fast reply/wait/busy/final/error tasks. Fast JSON triage intentionally skips the hook.
- Do not recreate `niko/agent.py` or `niko/agent_router.py`; the active graph lives under `niko/graphs/chat_reply/`.

## Commands

Use `rtk` for shell commands in this workspace.

```bash
rtk python -X utf8 -m unittest discover
rtk python -m bots.telegram.bot
```
