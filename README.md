# Niko Agent

![Niko Agent logo](assets/niko-logo.png)

Niko Agent la loi xu ly chinh dung Claude CLI (`fcc-claude`). Cac bot trong `bots/` chi lam gateway giao tiep: nhan tin, phan hoi nhanh, cho ket qua tu Niko roi gui lai cho nguoi dung.

## Cau Truc

```text
niko/                 # Core Niko Agent: route prompt, build context, goi Claude CLI
niko/.env.example     # Cau hinh rieng cua Niko core
bots/telegram/        # Telegram gateway: polling, mention, sticker, gui/nhan tin
HOOK.md               # Persona/hook nap vao Niko
stickers/ducks.json   # Mapping mood cho sticker Duck
```

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
CLAUDE_WORKDIR=.runtime/claude_sandbox
CLAUDE_TIMEOUT_SECONDS=180
CHAT_IDENTITY_ENABLED=1
CHAT_ALLOWED_USER_KEYS=
CHAT_USER_ALIASES=telegram:123456789=Anh A
```

`niko/.env` la cau hinh rieng cua Niko core:

```env
NIKO_AGENT_MODE=two_agent
NIKO_FAST_AGENT_COMMAND=
NIKO_FAST_AGENT_TIMEOUT_SECONDS=45
NIKO_UNCERTAIN_DELAY_SECONDS=3
NIKO_PROMPT_HOOK_FILE=HOOK.md
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

Bat `NIKO_AGENT_MODE=two_agent` de bot tra loi nhanh neu cau hoi don gian. Neu cau hoi can phan tich, Niko day sang deep agent chay background, Telegram van tiep tuc nhan tin va co the bao anh doi em chut.

`NIKO_FAST_AGENT_COMMAND` de trong thi fast agent chi dung rule noi bo nhu chao hoi, cam on, ping, ok, hoac cau khen ngan. Deep agent dung `CLAUDE_DEEP_AGENT_COMMAND`; neu de trong thi fallback ve `CLAUDE_CLI_COMMAND`.

## Sua Hook

Muon doi giong van thi sua `HOOK.md` va mo Pull Request. Khong can sua script Python.

## PR

Tao branch rieng, sua hook/code, mo Pull Request vao `main`. `main` la nhanh chinh, owner duyet va merge.
