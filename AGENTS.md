# Niko Agent Working Context

This repo is a local Python AI agent harness for Niko Agent. The current Deep
agent path calls DeepSeek's OpenAI-compatible Chat Completions API from
`niko/runtime.py`. Legacy Claude/FCC CLI helpers may remain for fallback or
debugging, but new Deep-agent work should not assume `fcc-claude` is the primary
runtime.

When talking to the project owner, use Vietnamese, refer to yourself as "em",
and refer to the user as "anh".

## Current Goal Of The Repo

Niko Agent is the runnable baseline for a graduation-project direction about
agent memory. The repo should first prove that a real harness can receive a
Telegram message, route the turn, call a local LLM runtime, record traces, store
basic memory, and show the run in an ops dashboard.

This repo is not the final lakehouse/knowledge-graph system yet. SQLite memory
and JSONL traces are intentionally simple baseline data. Later work can export
or transform them into a Semantic/Episodic Memory pipeline, lakehouse, graph
schema, and graph mining layer.

The current business direction is to keep Telegram as the conversation gateway
and introduce Jira as a future task/business-data gateway. The important story is
that Niko should eventually analyze real task/issue data rather than only the
scattered text a user pastes into chat.

Keep chat memory and business/lakehouse memory separate in docs and design:
Telegram chat memory is local interaction memory; Jira/lakehouse memory is a
separate business backend lane that Niko can retrieve from later.

The current research direction has expanded from chat memory alone toward an
Agent Harness for SDLC context handoff. Treat this as two related but separate
problems:

- Context handoff: collect fragmented context from Jira/issues, docs, chat,
  repo/code, meeting notes, and design notes; normalize it into a structured
  requirement/task packet; keep evidence, open questions, and human approval
  checkpoints.
- Task execution: once the packet/use case is clear, run an execution workflow
  that can produce design, code, tests, and traceability artifacts.

The near-term SDLC scope is steps 1-3 only: requirement intake/analysis, system
design/technical planning, and coding implementation. Human review is not an
afterthought: each phase can pause for the project owner to confirm, edit, or
redirect the artifact before the next phase consumes it.

Do not collapse this direction back into "optimize Jira storage". Jira,
Confluence/docs, GitHub/local repos, chat, and meeting notes are context
sources. The harness should prove controlled handoff first; lakehouse/KG storage
can come later when the workflow is worth scaling.

## Waku Reference Principle

Use `D:\Dự án cá nhân\Do_an_II\waku-agent` as a reference for responsibility
boundaries, not as a folder-name template to copy mechanically.

What Niko should learn from Waku:

- Gateway code stays thin: platform IO, auth, parsing, send/reply only.
- App/root code assembles settings, runtime dependencies, memory, tools, graphs,
  and owns the stable turn entrypoint.
- Graph/workflow code owns business flow and route topology.
- Loop code owns reusable tool-call mechanics, not application routing.
- Tool adapters are grouped by domain and never send platform replies directly.

Do not add a new package just because Waku has a nearby concept. Add a package
only when it owns real behavior, state, policy, workflow selection, or reusable
mechanics. Bridge-only layers that merely forward calls should be removed or
kept inside the existing assembly/root layer. In the current Niko baseline,
`NikoApp` is the Waku-style assembly and turn entrypoint, and now owns memory
correction plus Jira issue workflow selection. `niko/orchestration/` should not
exist again unless the selection logic outgrows `NikoApp` and needs an
independently testable owner.

## TraceDev Reference Principle

Use `D:\Dự án cá nhân\Do_an_II\ISSTA2026-TraceDev` as a reference for the lower
execution layer, not as the whole Niko architecture. TraceDev is best understood
as a straight requirement/use-case-to-code pipeline:

```text
structured use case
  -> Requirement Refiner
  -> Designer
  -> Validator traceability checks
  -> Developer
  -> Tester
  -> code/test/traceability artifacts
```

TraceDev's valuable lesson is its traceability-driven execution loop: it checks
whether requirements are represented in design/code and feeds missing links back
into the producing agent. It does not solve Niko's upper-layer problem of
turning fragmented Jira/docs/chat/repo context into a clean task packet. When
borrowing ideas from TraceDev, keep the split explicit:

```text
Niko context handoff layer
  fragmented collaboration context -> normalized task/use-case packet

TraceDev-style execution layer
  normalized packet/use case -> design -> code -> tests -> traceability
```

If a future workflow integrates TraceDev-like behavior, add checkpoints between
phases so the human can approve or alter requirement, design, and coding-plan
artifacts before downstream work continues.

## SDLC Harness Upgrade Direction

The next Niko upgrade direction is documented in
`docs/plans/2026-10-12-niko-sdlc-upgrade-plan.md` and its checklist. Treat that
plan as the local source of truth when working on SDLC integration.

The intended boundary is:

```text
Niko_Agent
  Telegram conversation control, decision routing, memory, status replies

SDLC_Harness
  source loading, requirement/design/code workflow, artifacts, checkpoints,
  traceability, and tool sync results
```

Niko should not directly load Jira/use-case source data for the SDLC workflow.
Niko should call SDLC_Harness, read its JSON status/checkpoint fields, and report
those back to the user. SDLC_Harness owns fixture/Jira/dataset source adapters.

The runtime upgrade target for this direction is **DeepSeek only**. The current
Deep-agent path already calls DeepSeek; remaining FCC/Claude code/config is
legacy fallback/debug. Do not add new FCC/Claude-dependent behavior for the SDLC
lane. Do not design this upgrade as a generic provider abstraction unless the
owner explicitly changes direction.

## Architecture Map

- `bots/telegram/`: thin Telegram gateway; platform IO/auth/parsing/reply only.
- `bots/decision_model/`: local Ollama/Nimble label decisions for routing,
  sticker mood, memory, correction, and Jira gates. No free-form user replies.
- `niko/chat_gateway.py` and `niko/gateway/`: channel-agnostic message handoff.
- `niko/app.py`: Waku-style assembly root and current turn entrypoint. It owns
  turn-level selection before delegating to workflows.
- `niko/graphs/chat_reply/`: main local/Fast/Deep chat workflow.
- `niko/graphs/jira_issue/`: read-only Jira issue analysis workflow, not a Jira
  bot and not lakehouse ingestion. This is now a legacy/experimental fixture
  lane for SDLC planning and should not be expanded as the main use-case source.
- `niko/loop/`: reusable tool-loop core.
- `niko/tools/<domain>/`: Loop tool adapters such as memory and Jira.
- `niko/memory/`: SQLite chat memory baseline and correction/consolidation
  runtime.
- `niko/sdlc/`: adapter từ Niko sang repo `../SDLC_Harness`; Niko chỉ gọi CLI/API
  workflow và nhận JSON trạng thái, không tự kéo use case từ Jira.
- `niko/runtime.py`: DeepSeek runtime cho Deep agent chính; Claude/FCC CLI helper
  là legacy fallback/debug.
- `niko/harness/` and `niko/ops/`: traces, runtime logs, dashboard, config, bot
  controls.
- `niko/.runtime/`: local runtime state. Do not commit.
- `docs/`: local-only planning/research docs. Do not stage or commit unless the
  owner explicitly changes policy.

Important boundary: Niko is a runnable baseline harness, not a generic agent
framework core. Avoid bridge-only layers. `NikoApp` may coordinate a turn; real
workflows belong under `niko/graphs/`, reusable loop mechanics under
`niko/loop/`, and tools under `niko/tools/<domain>/`.

## Chat Flow

Telegram turns follow this high-level path:

```text
bots/telegram
  -> ChatGatewayMessage
  -> GatewayRunner
  -> NikoApp
  -> memory correction / Jira workflow / chat reply graph
  -> reply
```

In `two_agent` mode:

- local rules answer trivial greetings/thanks/ping/praise;
- Nimble can decide `reply_now` vs `send_to_deep` for gray-zone prompts;
- Fast/Fable may produce quick replies when Nimble chooses `reply_now`;
- Deep handles careful work, code, tools, Jira, memory-heavy questions, and
  uncertain context;
- Fast triage/reply receives a short recent `chat_log` working-memory window;
- Deep receives working memory plus long-term memory according to settings.
- When `NIKO_JIRA_TOOLS_ENABLED=1`, `NikoApp` can route prompts with Jira issue
  keys through `niko/graphs/jira_issue/` before normal chat. The workflow fetches
  fixture data with Loop, adds evidence context to Deep, and returns a safe
  no-data reply when the issue key is not in the fixture. Clear issue keys are
  handled by Python rule; when `NIKO_JIRA_DECISION_GATE_ENABLED=1`, ambiguous
  prompts such as "ticket vừa nãy" can ask Nimble to choose `use_jira_tool`,
  `ask_for_issue_key`, or `skip_jira`.
- When Deep finishes, the result can pass through Fast final composition before
  being sent to the user.
- If Deep is already busy in the same conversation, new messages are appended to
  the active job followups and the bot returns `busy_reply`.

Nimble triage receives the current prompt, light gateway metadata, and a short
`recent_turns` working-memory window. It should choose `send_to_deep` when the
turn depends on missing/uncertain context, tools, Jira, code, memory, or careful
analysis. If Nimble still chooses `reply_now` with confidence below
`NIKO_FAST_TRIAGE_REPLY_CONFIDENCE_THRESHOLD`, `ChatReplyGraph` overrides that
choice to `send_to_deep` so Fast does not answer blindly. Legacy Fable JSON
triage still exists as a fallback path when the decision model is disabled, but
new triage work should prefer `bots/decision_model/`.

## Memory Baseline

SQLite currently stores:

- `chat_log`: operational conversation history;
- `facts`: semantic memory baseline;
- `episodes`: episodic memory baseline.

Important boundaries:

- `chat_log` is not long-term semantic memory by itself.
- Working memory is ephemeral recent conversation context rebuilt from
  `chat_log`.
- Semantic extraction is conservative and still immature.
- Retrieval is text-based FTS/LIKE, not embeddings/reranking/graph reasoning.
- The local decision model may choose labels/modes, but Python owns all SQLite
  mutations.
- Consolidation exists in manual/default-off auto paths; inspect
  `niko/memory/` for exact current behavior.
- Memory correction treats `current_prompt` as the primary evidence. Recent
  turns are only attached when the current prompt has an explicit sửa/xóa/quên
  signal or is a pending fact-ID follow-up. Neutral prompts must not inherit old
  correction context. When a reply only selects a pending fact ID, Python
  validates it against the pending choices and uses the pending action without
  calling the model again. Python still performs all update/delete operations.
  Ambiguous fact choices are also saved in `memory_correction_pending` with a
  15-minute TTL, so `fact #...` follow-ups can survive a new `MemoryRuntime`
  instance. This correction flow is still a Phase 5 V1 temporary chat baseline.
  Phase 4A/4B add a default-off loop bridge for direct correction prompts and
  durable pending state, but the pending follow-up facade remains V1 until the
  generic Loop/tool slot owns the whole correction workflow.
- Loop core V0 exists in `niko/loop/`, memory fact tool adapters live in
  `niko/tools/memory/facts.py`, and read-only Jira fixture tools live in
  `niko/tools/jira/`. Correction loop V0 is still behind
  `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=1`. This does not make a complete tool
  router or Jira bot.

## Business Domains And Future Jira Gateway

- Telegram is the current real-time conversation gateway.
- Jira is the planned task/issue gateway, but the current fixture/tool lane in
  Niko is legacy for the SDLC direction. New use-case/source loading should live
  in SDLC_Harness, with Niko reading SDLC status instead of reading Jira itself.
- Memory upgrade work should explain why reading scattered prompt text is not
  enough for task analysis.
- Distinguish runtime Jira tools, a future Jira bot/gateway, and the separate
  lakehouse/KG data-processing direction.

```text
User asks about a Jira issue key
  -> Niko parses the issue key
  -> Loop/tool slot fetches issue/comment/changelog/component data
  -> context is normalized
  -> Deep agent analyzes with evidence
  -> trace/ops records the run
```

The issue key can come from Jira Cloud Free, a mock Jira JSON fixture, or a
public dataset. Do not make a paid Jira subscription a requirement for the first
demo.

## Trace And Ops Dashboard

Trace events are JSONL and should make route, memory, tool, Deep/Fast, and
reply decisions observable. Use trace/runtime logs instead of terminal-only
printing for operational behavior.

Run the dashboard locally before starting bots:

```bash
rtk python -m niko.ops.dashboard
```

The dashboard `Bots` tab is the preferred place to start/stop Telegram Bot,
warm up or stop/unload Decision Model, and inspect runtime logs. Terminal output
should stay bootstrap-only; operational logs belong in `niko/harness/runtime_log.py`.
Telegram Bot uses `niko/.runtime/telegram_bot.lock` as a single-instance guard;
dashboard should show `external` instead of starting a duplicate when a terminal
bot process already owns that lock.

## Docs Policy

`docs/` is local-only working documentation. It may contain architecture notes,
plans, diagrams, translated papers, and live-test logs. It is intentionally
ignored by git and has been removed from git tracking.

Use local docs when present, especially `docs/agent-harness-sdlc/README.md` for
the SDLC context-handoff direction and `docs/plans/` for local checklists. Do
not treat missing docs in a fresh clone as a code failure, and do not re-add docs
to git unless the owner explicitly asks.

## Environment And State

Dashboard-first config precedence:

```text
real OS environment -> niko/.runtime/config.json -> root .env bootstrap -> code defaults
```

The dashboard Config tab writes `niko/.runtime/config.json`. Keep secrets and
machine-specific commands in dashboard runtime config for local demo, unless an
OS env override is intentionally needed. Dashboard snapshots must mask secrets.
Root `.env` is only for dashboard bootstrap keys such as `NIKO_OPS_HOST`,
`NIKO_OPS_PORT`, and `NIKO_RUNTIME_CONFIG_FILE`. Do not recreate `niko/.env` or
`bots/telegram/.env`; put Telegram, agent, decision model, sticker, memory, and
reply settings in the dashboard Config tab. Dashboard-spawned bot subprocesses
must use `runtime_subprocess_env()` so file-env keys are not inherited as OS
overrides.

For exact config keys, inspect `niko/ops/config_schema.py`, `niko/config.py`,
and the dashboard Config tab. Do not keep a full config-key catalog in this
bootstrap file.

Do not reveal or commit secrets from `.env` files, Telegram tokens, runtime
config, runtime SQLite data, trace contents, or runtime logs that may contain
private conversation data.

## Development Commands

Use `rtk` for shell commands in this workspace.

```bash
rtk python -m pytest
rtk python -m bots.decision_model.warmup
rtk python -m bots.telegram.bot
rtk python -m niko.ops.dashboard
```

Preferred manual run order is dashboard first, then use tab `Bots` to warm up
Decision Model and start Telegram Bot. Direct bot/warmup commands remain useful
for debugging.

If calling PowerShell cmdlets through `rtk`, invoke PowerShell explicitly:

```bash
rtk powershell -NoProfile -Command "Get-Content -Raw -LiteralPath 'AGENTS.md'"
```

## Code Navigation

This repo has a `.codegraph/` index. When trying to understand or locate code,
use CodeGraph before broad grep/search:

```bash
rtk codegraph explore "ChatReplyGraph MemoryStore TraceLogger dashboard"
```

Use `rg`/`rg --files` for plain text search or file listing when CodeGraph is
not relevant.

## Editing Rules For Future Sessions

- Preserve the thin gateway boundary: Telegram should stay as IO/auth/parsing.
- Keep `niko/gateway/` channel-agnostic. `niko/app.py` is allowed to own
  turn-level workflow selection; keep route labels and trace events compatible
  unless a plan explicitly changes them.
- Avoid creating bridge-only packages such as a turn orchestrator that only
  delegates. Match Waku's spirit: `app.py` may coordinate a turn, while real
  workflows live under `niko/graphs/` and reusable loop mechanics live under
  `niko/loop/`.
- Do not move additional workflow selection responsibilities without an explicit
  plan. If local `docs/plans/` exists, update the relevant checklist; otherwise
  explain the plan in the response and keep the change small.
- Put Ollama/Nimble decision-model behavior in `bots/decision_model/`, not in the
  Telegram gateway. Nimble is for route/label decisions, not free-form reply
  generation.
- Put routing/agent-loop behavior in `niko/graphs/chat_reply/`.
- When adding reusable Loop mechanics, put them in `niko/loop/`. Put ToolRegistry
  adapters in `niko/tools/<domain>/`; keep actual persistence/runtime ownership
  in the domain package, such as `niko/memory/` for SQLite memory.
- Put DeepSeek/LLM invocation details in `niko/runtime.py`.
- Put memory persistence/retrieval in `niko/memory/`.
- Put observability-only behavior in `niko/harness/` or `niko/ops/`.
- Do not recreate old entry points such as `niko/agent.py` or
  `niko/agent_router.py`; the active graph is under `niko/graphs/chat_reply/`.
- Keep `niko/.runtime/` out of git.
- Keep `docs/` out of git. It is local planning/research material for this
  machine, now ignored by `.gitignore`; do not stage it or remove the ignore
  rule unless the owner explicitly asks to publish docs again.
- Keep the SDLC context-handoff direction separate from TraceDev-style
  execution. Build/plan checkpoints and normalized artifacts before assuming the
  system can run directly from raw Jira/docs/chat context to code.
- Prefer small, test-covered changes. Existing tests cover routing, Telegram
  prompt behavior, memory store, trace logger, and dashboard snapshot.
<!-- NIKO_SDLC_SYNC_START -->
## Latest SDLC Sync Boundary

Niko can start, inspect, and approve the current static SDLC checkpoint when the
user gives a clear run id. Phase 1/2 integration is approve-only: Niko does not
edit SDLC artifacts or handle change requests yet. Keep this boundary strict:

```text
Telegram/user prompt
  -> Niko detects an SDLC intent
  -> Niko calls SDLC_Harness CLI JSON
  -> SDLC_Harness loads Jira/fixture source, refines requirements, validates,
     writes runtime artifacts, syncs Jira/Confluence, and returns a compact
     machine-readable result
  -> Niko formats the result for chat
```

Do not duplicate SDLC source loading, requirement refinement, artifact writing,
or Jira/Confluence sync inside Niko. If richer SDLC behavior is needed, extend
the SDLC_Harness contract first, then adapt `niko/sdlc/`. The old read-only Jira
fixture workflow in Niko is only for issue analysis, not the SDLC requirement
pipeline.

Current Telegram SDLC actions are `start`, `get_status`, and
`approve_checkpoint` with an explicit `run_id`. Do not expose `request_change`,
`cancel_run`, `resume_run`, or dynamic context invalidation until the matching
SDLC_Harness phase exists and its JSON contract is extended. Niko should still
surface SDLC `blocked`/sync-partial states clearly by reading `last_error` and
`last_sync`; this is status reporting, not artifact mutation.
<!-- NIKO_SDLC_SYNC_END -->

