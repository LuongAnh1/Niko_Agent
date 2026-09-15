# Niko Agent Architecture

Niko Agent la mot Python project dung Claude CLI thay cho API LLM truc tiep. Hien tai gateway chinh la Telegram, con luong dieu phoi chat nam trong `niko/graphs/chat_reply/`.

## Muc Tieu Hien Tai

- Nhan tin nhan tu Telegram.
- Chi xu ly group message khi co tag bot, tru lenh `/id`.
- Tach phan giao tiep nhanh va phan xu ly sau bang hai agent.
- Goi Claude CLI qua `fcc-claude` trong runtime sandbox.
- Chua dung Working Memory, RAG, Tool Router hay Memory Layer rieng.

## Bo Cuc Repo

```text
bots/
  telegram/
    bot.py             # Telegram gateway: polling, auth, mention filter, /id, reply, sticker
    sticker_picker.py  # Local sticker picker cho Telegram Duck

niko/
  runtime.py           # Goi Claude CLI, doc hook, build prompt, deep agent command
  config.py            # Load env va resolve project path
  chat_gateway.py      # ChatGatewayMessage, identity, alias, allowed user parsing
  HOOK.md              # Persona/hook nap vao Niko
  graphs/
    chat_reply/
      graph.py         # ChatReplyGraph dieu phoi flow chat
      router.py        # Rule router local/deep/fast/busy
      prompts.py       # Prompt task cho Fast Agent va final compose

stickers/
  ducks.json           # Mapping mood/keyword sang sticker Telegram
```

## Ranh Gioi Trach Nhiem

`bots/telegram` la gateway. No chi nen biet Telegram API, message shape, mention filter, `/id`, auth, send message va send sticker. Gateway khong nen chua logic quyet dinh LLM nao xu ly.

`niko.graphs.chat_reply` la graph nghiep vu chat. No quyet dinh tin nhan nao tra loi local, tin nao cho Fast Agent triage, tin nao day Deep Agent, va cach compose ket qua sau khi Deep xong.

`niko.runtime` la lop goi Claude CLI. No doc env command, resolve `CLAUDE_WORKDIR`, nap `niko/HOOK.md`, chen identity context neu bat `CHAT_IDENTITY_ENABLED`, roi goi CLI.

`niko.chat_gateway` la abstraction chung cho cac cong chat. Neu sau nay them Zalo/Discord, gateway moi nen convert message ve `ChatGatewayMessage` roi goi graph tuong tu Telegram.

## Import Chinh

```python
from niko.graphs.chat_reply import ChatReplyGraph
```

Telegram gateway dang tao mot instance global:

```python
CHAT_REPLY_GRAPH = ChatReplyGraph()
```

## Env Chinh

Root `.env` giu cau hinh chung:

- `CLAUDE_CLI_COMMAND`
- `CLAUDE_DEEP_AGENT_COMMAND`
- `CLAUDE_WORKDIR`
- `CLAUDE_TIMEOUT_SECONDS`
- `CHAT_IDENTITY_ENABLED`
- `CHAT_ALLOWED_USER_KEYS`
- `CHAT_USER_ALIASES`

`niko/.env` giu cau hinh agent:

- `NIKO_AGENT_MODE`
- `NIKO_FAST_AGENT_COMMAND`
- `NIKO_FAST_AGENT_TIMEOUT_SECONDS`
- `NIKO_UNCERTAIN_DELAY_SECONDS`
- `NIKO_DEEP_WAIT_REPLY`
- `NIKO_DEEP_BUSY_REPLY`
- `NIKO_PROMPT_HOOK_FILE`
- `NIKO_REPLY_SUFFIX`
- `NIKO_TOOL_UNAVAILABLE_REPLY`

`bots/telegram/.env` giu cau hinh Telegram:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `TELEGRAM_DROP_PENDING_UPDATES`
- `TELEGRAM_GROUP_MODE`
- `TELEGRAM_MENTION_REPLIES`
- `TELEGRAM_STICKERS_ENABLED`
- `TELEGRAM_STICKER_CONFIG_FILE`
- `TELEGRAM_STICKER_SET_NAME`
- `TELEGRAM_STICKER_MODE`

Thu tu load env hien tai: root `.env` -> `niko/.env` -> `bots/telegram/.env`. Bien moi truong that cua OS van uu tien hon file `.env`.

## Huong Mo Rong

- Them gateway moi: tao folder trong `bots/`, parse message ve `ChatGatewayMessage`, roi goi `ChatReplyGraph`.
- Them nghiep vu moi: tao graph moi trong `niko/graphs/`.
- Them memory/RAG/tool: nen them thanh node/layer rieng trong graph, khong dua vao Telegram gateway.
- Neu can cap tai lieu co dinh cho agent doc, nen de harness/memory/RAG chen phan lien quan vao prompt thay vi de Claude CLI tu quet repo.
