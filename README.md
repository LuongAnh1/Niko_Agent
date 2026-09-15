# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Niko Agent dung Claude CLI (`fcc-claude`) thay cho viec goi API LLM truc tiep. Bot Telegram chi la gateway; workflow tra loi chat nam trong `niko/graphs/chat_reply/`, con `niko/runtime.py` giu phan goi Claude CLI va hook/persona.

## Cau Truc

```text
niko/runtime.py              # Claude CLI runtime, prompt hook, command config, deep call
niko/graphs/chat_reply/      # Chat reply graph: route, fast triage, deep handoff, final compose
niko/.env.example            # Cau hinh rieng cua Niko runtime/agent
niko/.runtime/               # Vung chay tam cua Niko, khong commit
niko/HOOK.md                 # Persona/hook nap vao Niko
bots/telegram/               # Telegram gateway: polling, mention, /id, sticker, gui/nhan tin
stickers/ducks.json          # Mapping mood cho sticker Duck
```

## Tai Lieu

- docs/architecture.md: tong quan kien truc va ranh gioi module.
- docs/telegram-chat-flow.md: luong xu ly tin nhan Telegram voi hai agent.
- AGENTS.md: context ngan cho Codex khi mo phien chat moi.

## Chay Local

1. Tao bot Telegram bang [@BotFather](https://t.me/BotFather) va lay token.
2. Copy `.env.example` thanh `.env`, dien cau hinh chung cho Claude/identity.
3. Copy `niko/.env.example` thanh `niko/.env`, dien cau hinh rieng cua Niko.
4. Copy `bots/telegram/.env.example` thanh `bots/telegram/.env`, dien cau hinh Telegram.
5. Cai va cau hinh Free Claude Code/FCC. Neu `fcc-claude` tro qua FCC thi chay `fcc-server` truoc.
6. Chay bot:

```bash
python -m bots.telegram.bot
```

## Lay ID

Dung `/id` trong private chat hoac `/id@TenBot` trong group de lay `chat_id` va `user_key`. Trong group, mac dinh bot chi xu ly tin nhan co tag `@TenBot`. Khi tra loi, bot se mention nguoi vua goi neu `TELEGRAM_MENTION_REPLIES=1`.

## Env

Root `.env` la cau hinh chung cua he thong:

```env
CLAUDE_CLI_COMMAND=fcc-claude -p
CLAUDE_DEEP_AGENT_COMMAND=fcc-claude --bare --no-session-persistence --tools= -p
CLAUDE_WORKDIR=niko/.runtime/claude_sandbox
CLAUDE_TIMEOUT_SECONDS=180
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A
```

`niko/.env` la cau hinh rieng cua Niko:

```env
NIKO_AGENT_MODE=two_agent
NIKO_FAST_AGENT_COMMAND=fcc-claude --model fable --bare --no-session-persistence --tools "" -p
NIKO_FAST_AGENT_TIMEOUT_SECONDS=45
NIKO_UNCERTAIN_DELAY_SECONDS=3
NIKO_PROMPT_HOOK_FILE=niko/HOOK.md
NIKO_REPLY_SUFFIX=Meow
```

`bots/telegram/.env` chi la cau hinh gateway Telegram:

```env
TELEGRAM_BOT_TOKEN=token_cua_bot
TELEGRAM_ALLOWED_CHAT_IDS=-100xxxxxxxxxx
TELEGRAM_GROUP_MODE=mentions
TELEGRAM_MENTION_REPLIES=1
TELEGRAM_STICKERS_ENABLED=1
```

Thu tu load env: root `.env` -> `niko/.env` -> `bots/telegram/.env`. Bien moi truong that cua he dieu hanh van duoc uu tien hon file `.env`.

## Hai Agent

Bat `NIKO_AGENT_MODE=two_agent` de tach vai tro:

```text
Telegram gateway -> niko.graphs.chat_reply -> Niko Fast / Niko Deep -> niko.graphs.chat_reply -> Telegram gateway
```

Niko Fast la mat giao tiep nhanh va lop triage cho cac cau hoi khong chac. Neu Fast thay co the tra loi ngay, Fast tra loi truc tiep. Neu Fast thay can phan tich, can tool/memory/tai lieu, hoac khong chac, Niko Deep chay background; Fast gui cau bao doi/bao ban trong luc cho.

Khi Niko Deep xu ly xong, ket qua noi bo se quay lai Niko Fast truoc. Fast compose thanh cau tra loi tu nhien cho nguoi dung, roi gateway moi gui ra chat.

`NIKO_FAST_AGENT_COMMAND` nen tro toi model nhe/nhanh. De trong thi he thong fallback ve rule/template local cho wait/busy va gui ket qua deep truc tiep. Deep agent dung `CLAUDE_DEEP_AGENT_COMMAND`; neu de trong thi fallback ve `CLAUDE_CLI_COMMAND`.

## Sua Hook

Muon doi giong van thi sua `niko/HOOK.md` va mo Pull Request. Khong can sua script Python.

## PR

Tao branch rieng, sua hook/code, mo Pull Request vao `main`. `main` la nhanh chinh, owner duyet va merge.
