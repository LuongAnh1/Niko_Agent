# Niko Agent Working Context

This repo is a local Python AI agent harness for Niko Agent. Niko does not call an
LLM API directly from the application code. The current runtime shells out to
Claude/FCC through `fcc-claude` on the developer's machine.

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

## Architecture Map

- `bots/telegram/`: Telegram gateway. Handles long polling, message parsing,
  auth/allowlist, mention filtering, `/id`, `/whoami`, replies, and stickers.
  Keep this layer thin. It should not own memory, routing, or LLM policy.
- `bots/decision_model/`: Local Ollama/Nimble decision scripts. Owns the
  `/v1/systemone` client, route-label mapping, sticker mood, memory retrieval/write
  decisions, memory candidate classification, Jira decision gate, and warmup
  script for keeping the model loaded. This layer decides labels/modes only; it
  should not generate free-form user replies.
- `bots/decision_model/memory/`: Memory-specific Decision Model package. Keep
  retrieval, write, candidate, and correction prompts/normalizers in separate
  files here so each decision surface can be debugged independently. Public
  imports should keep working through `bots.decision_model.memory`.
- `niko/chat_gateway.py`: Normalizes channel-specific messages into
  `ChatGatewayMessage` and identity context.
- `niko/gateway/`: Phase 1 gateway runner. Owns the channel-agnostic handoff
  from normalized gateway messages/callbacks into the current chat workflow.
  For now it delegates directly to `ChatReplyGraph` and must stay behavior
  preserving; it is not the future app orchestrator yet.
- `niko/loop/`: Generic tool-loop core V0. Owns Tool/ToolRegistry/LoopResult,
  LoopRuntime, and observer mechanics. Keep it independent from Telegram and
  from any single domain workflow.
- `niko/tools/`: Tool adapters for Loop. Keep adapters here, grouped by domain
  such as `memory/` and `jira/`. Tools follow the `niko.loop.Tool` contract and
  should fetch/normalize data or mutate only through explicit domain guardrails;
  tools must not send Telegram/Jira replies or own workflow policy.
- `niko/graphs/chat_reply/`: Main chat business graph. Owns routing, local
  replies, Fast/Deep handoff, background deep jobs, busy replies, followups,
  final reply composition, memory writes, and trace events.
- `niko/graphs/jira_issue/`: Phase 6B Jira issue analysis workflow. It wraps
  Jira Loop tools, formats issue/comment/changelog context with evidence, and
  hands the context to Deep. Phase 6C adds a default-off Decision Model gate for
  ambiguous Jira/task prompts. It is not a Jira bot/gateway and does not write
  Jira data into chat memory SQLite.
- `niko/runtime.py`: Claude CLI runtime. Loads env, reads `niko/HOOK.md`,
  injects identity/memory context, resolves `CLAUDE_WORKDIR`, and calls
  `fcc-claude`.
- `niko/harness/trace.py`: JSONL trace logger. Default trace path is
  `niko/.runtime/traces/YYYY-MM-DD.jsonl`.
- `niko/harness/runtime_log.py`: JSONL runtime log logger for Bots dashboard
  table. Default log path is `niko/.runtime/logs/YYYY-MM-DD.jsonl`.
- `niko/memory/store.py`: SQLite memory store for `chat_log`, `facts`, `episodes`,
  and consolidation state. Uses FTS5 when available, with LIKE fallback.
- `niko/memory/runtime.py`: MemoryRuntime pipeline for retrieval gate,
  retrieval modes (`search/list/recent/none`), recent working-memory context,
  write gate, memory correction workflow facade, store search/list, chat log
  writes, episode writes, manual/auto consolidation facade, and context
  formatting before Deep.
- `niko/memory/context.py`: RetrievedMemory result type, formatter helpers, and
  compatibility wrappers such as `retrieve_memory_context(...)`.
- `niko/memory/working_memory.py`: Ephemeral recent conversation window builder
  from `chat_log`, shared by Deep prompt context and correction decision context.
- `niko/memory/correction_workflow.py`: Phase 5 V1 chat memory correction
  workflow. Owns pending fact choices, fact match/apply guardrails, and
  durable pending state in SQLite while `MemoryRuntime` keeps the public facade.
- `niko/memory/correction_loop.py`: Phase 4A default-off bridge from correction
  prompts to LoopRuntime + memory fact tools. It handles direct correction
  prompts when `NIKO_MEMORY_CORRECTION_LOOP_ENABLED=1`, and falls back to V1 on
  loop errors.
- `niko/tools/memory/facts.py`: Memory fact tools for Loop core V0. Owns
  `search_facts`, `list_facts`, `update_fact`, and `delete_fact` adapters over
  `MemoryStore`; direct correction prompts can use them through the default-off
  correction loop bridge. The old `niko/memory/loop_tools.py` compatibility
  shim has been removed; new imports should use `niko/tools/memory/facts.py`.
- `niko/tools/jira/`: Read-only Jira fixture tools V0 for parsing issue keys and
  fetching issue/comment/changelog data through Loop. This is a runtime tool lane,
  not the lakehouse/KG ingestion layer and not a Jira bot yet.
- `niko/ops/dashboard.py`: Thin stdlib HTTP entrypoint for Niko Ops dashboard.
- `niko/ops/bots.py`: Dashboard bot controls for Telegram Bot and Decision Model.
- `niko/ops/config_schema.py`: Config tab schema, validation, masking, snapshots.
- `niko/ops/templates/dashboard.html`: Dashboard HTML/CSS/JS frontend template.
- `niko/HOOK.md`: Niko persona and operating instructions loaded into agent
  prompts, except for Fast JSON triage.
- `niko/.runtime/`: Local runtime state. Do not commit it.
- `docs/`: Architecture, flow, memory/ops, business-domain, demo, and roadmap
  documents.

Important current boundary: Niko has a runnable baseline harness, not a generic
agent framework core yet. `GatewayRunner` is only a thin bridge into the existing
chat graph. `ChatReplyGraph` is still a hand-written business graph for chat,
not a reusable Node/Edge/Workflow engine like Waku's graph runtime.
The target split for gateway runner, app assembly, turn orchestrator, graphs,
Loop, and tools started with the Phase 1 `niko/gateway/` runner. The broader
target is documented in `docs/plans/2026-10-08-niko-core-split-survey.md`; do
not assume `NikoApp` or the turn orchestrator exist until later phases create
them. The phase-by-phase implementation direction now lives in
`docs/plans/2026-10-08-niko-core-split-implementation-plan.md`, with checklist
tracking in `docs/plans/2026-10-08-niko-core-split-implementation-checklist.md`.

## Chat Flow

The Telegram gateway converts each accepted Telegram message into
`ChatGatewayMessage`, then calls:

```python
from niko.gateway import GatewayRunner
```

`GatewayRunner` currently forwards the turn to `ChatReplyGraph` without changing
route labels, trace events, memory behavior, or Telegram callbacks.

`ChatReplyGraph` runs either `single` or `two_agent` mode according to
`NIKO_AGENT_MODE`.

In `two_agent` mode:

- Local rules answer simple greetings/thanks/ping/praise messages quickly.
- Deep keywords, long prompts, newlines, or code blocks route to Niko Deep.
- Gray-zone prompts can route to the local Ollama/Nimble decision model when
  `NIKO_DECISION_MODEL_ENABLED=1`.
- Nimble decision `reply_now` means the graph should answer quickly. If the
  decision does not include reply text, the graph can call Niko Fast/Fable via
  `NIKO_FAST_AGENT_COMMAND` to generate the quick reply.
- Nimble decision `send_to_deep` queues a Deep background job and sends a wait
  reply.
- Deep receives memory context only when memory retrieval is enabled.
- When `NIKO_JIRA_TOOLS_ENABLED=1`, prompts with Jira issue keys can run through
  `niko/graphs/jira_issue/` before Deep. The workflow fetches fixture data with
  Loop, adds evidence context to Deep, and returns a safe no-data reply when the
  issue key is not in the fixture. Clear issue keys are handled by Python rule;
  when `NIKO_JIRA_DECISION_GATE_ENABLED=1`, ambiguous prompts such as "ticket vừa
  nãy" can ask Nimble to choose `use_jira_tool`, `ask_for_issue_key`, or
  `skip_jira`.
- When Deep finishes, the result can pass through Fast final composition before
  being sent to the user.
- If Deep is already busy in the same conversation, new messages are appended to
  the active job followups and the bot returns `busy_reply`.

Nimble triage intentionally receives only prompt and light gateway metadata, not
memory context. This keeps route decisions clean and fast. Legacy Fable JSON
triage still exists as a fallback path when the decision model is disabled, but
new triage work should prefer `bots/decision_model/`.

## Memory Baseline

SQLite currently stores three groups of data:

- `chat_log`: operational conversation log for user/assistant messages.
- `facts`: Semantic Memory baseline, added manually through Ops/API or through
  conservative consolidation of explicit chat facts.
- `episodes`: Episodic Memory baseline, mostly completed Deep jobs and followups.

Important boundaries:

- `chat_log` is not the same thing as Semantic/Episodic Memory. It is an
  operational log that can later feed analysis.
- Working memory is ephemeral: Deep can receive a short recent conversation
  window rebuilt from `chat_log` for the current turn, but that window is not
  Semantic/Episodic long-term memory.
- Semantic extraction is not mature yet. Facts are mainly added through Ops/API
  and explicit consolidation, either manual `Run once` or default-off auto; free-form
  summarizer extraction is not built yet.
- Episodic records are basic summaries of deep work, not rich event models yet.
- Retrieval is text-based FTS/LIKE, not embeddings, reranking, or graph
  reasoning. The local Decision Model can choose skip/retrieve/list facts/recent
  episodes and retrieval modes, but it does not write SQLite data itself.
- Consolidation has manual and default-off auto paths. Dashboard/API `Refresh
  batch` previews the next batch, and `Run once` processes one batch. Auto
  consolidation only runs when `NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED=1`, after
  enough complete user/assistant exchanges, in a background worker guarded by a
  single-process lock. Wait/busy assistant replies do not count as completed
  exchanges.
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

Docs under `docs/business-domains/` describe the product/business framing:

- Telegram is the current real-time conversation gateway.
- Jira is the planned task/issue gateway for Phase 2.
- Memory upgrade work should explain why reading scattered prompt text is not
  enough for task analysis.
- Distinguish three Jira-related lanes: runtime Jira tools under `niko/tools/jira/`
  plus `niko/graphs/jira_issue/` for direct issue analysis, a future Jira
  bot/gateway that lives on Jira, and the separate lakehouse/KG repo that
  ingests/processes Jira data for analysis.

Expected Phase 2 story:

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

Trace events are JSONL and should make a turn observable. Typical events include
`turn_start`, `route_decision`, `memory_retrieval`, `memory_gate_decision`,
`memory_correction_decision`, `memory_correction_clarify`,
`memory_correction_applied`, `memory_write_chat_log`, `memory_write_decision`,
`memory_write_episode`, `deep_job_started`, `deep_agent_call_started`,
`deep_agent_call_finished`, `reply_delivered`, errors, and `turn_end`.

Memory gate trace/runtime logs should include decision/label/query plus
`fact_mode` and `episode_mode`, so inventory turns can be debugged without
guessing from raw prompt text.

Run the dashboard locally before starting bots:

```bash
rtk python -m niko.ops.dashboard
```

Default URL:

```text
http://127.0.0.1:7777
```

Dashboard graph semantics:

- `Gateway -> Router -> Reply`: local or busy reply.
- `Gateway -> Router -> Fast Agent -> Reply`: Fast reply path.
- `Gateway -> Router -> Memory Gate -> Loop/Deep Agent -> Reply`: Deep path.
- `Memory Gate -> Memory Records`: retrieval from facts/episodes.
- `Reply/turn events -> Trace/Ops`: observer path, not part of agent reasoning.

The dashboard `Bots` tab is the preferred place to start/stop Telegram Bot,
warm up or stop/unload Decision Model, and inspect runtime logs. Terminal output
should stay bootstrap-only; operational logs belong in `niko/harness/runtime_log.py`.
Telegram Bot uses `niko/.runtime/telegram_bot.lock` as a single-instance guard;
dashboard should show `external` instead of starting a duplicate when a terminal
bot process already owns that lock.

## Important Docs

- `README.md`: high-level setup and baseline explanation.
- `docs/harness/architecture.md`: repo layout, module boundaries, and runtime state.
- `docs/harness/telegram-chat-flow.md`: Telegram routing and two-agent behavior.
- `docs/harness/memory-ops.md`: SQLite memory, trace, and dashboard.
- `docs/harness/memory-eval-scenarios.md`: deterministic chat memory eval
  scenarios for retrieval/write/correction regression checks.
- `docs/loop/architecture.md`: target architecture for the generic Niko Loop,
  ToolRegistry, observer events, and first memory/Jira tool workflows.
- `docs/business-domains/README.md`: Telegram gateway, planned Jira gateway, and
  memory upgrade business context.
- `docs/demo/demo-guide.md`: demo script for showing the harness to a supervisor.
- `docs/plans/2026-10-07-chat-memory-decision-model.md`: short-term chat memory
  implementation plan using the local Decision Model.
- `docs/plans/2026-10-07-chat-memory-decision-model-checklist.md`: phase-by-phase
  checklist and live verification status for chat memory work.
- `docs/plans/2026-10-07-chat-memory-live-test-checklist.md`: concrete live-test
  checklist for current Phase 6/7 memory flow verification.
- `docs/plans/2026-10-08-niko-loop-implementation-plan.md`: implementation plan
  for the generic Loop runtime.
- `docs/plans/2026-10-08-niko-loop-implementation-checklist.md`: phase checklist
  for Loop docs, core runtime, memory tools, dashboard observability, and Jira lane.
- `docs/plans/2026-10-08-docs-source-sync-checklist.md`: audit checklist for
  keeping Markdown docs and source file comments aligned with the current repo state.
- `docs/plans/2026-10-08-niko-core-split-survey.md`: survey plan for separating
  gateway runner, app assembly, turn orchestration, graphs, Loop, and tools.
- `docs/plans/2026-10-08-niko-core-split-survey-checklist.md`: checklist for
  the core/gateway/graph split survey and docs sync.
- `docs/plans/2026-10-08-niko-core-split-implementation-plan.md`: phase-by-phase
  implementation direction for actually refactoring gateway runner, app assembly,
  turn orchestrator, Jira workflow selection, and memory correction selection.
- `docs/plans/2026-10-08-niko-core-split-implementation-checklist.md`: checklist
  to tick after each core/gateway/graph split phase is implemented and verified.
- `docs/memory/chat-memory-architecture-flow.md`: current/target memory
  architecture and retrieval/write/consolidation/correction flow diagrams.
- `docs/memory/roadmap.md`: path from baseline memory to lakehouse/KG work.

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

Core runtime config keys:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=
CLAUDE_CLI_COMMAND=fcc-claude -p
CLAUDE_DEEP_AGENT_COMMAND=fcc-claude --bare --no-session-persistence --tools= -p
CLAUDE_WORKDIR=niko/.runtime/claude_sandbox
NIKO_AGENT_MODE=two_agent
NIKO_DECISION_MODEL_ENABLED=1
NIKO_DECISION_MODEL_BASE_URL=http://localhost:11434
NIKO_DECISION_MODEL_NAME=nimble
NIKO_DECISION_MODEL_TIMEOUT_SECONDS=10
NIKO_DECISION_MODEL_KEEP_ALIVE=-1
NIKO_FAST_AGENT_COMMAND=fcc-claude --model fable --bare --no-session-persistence --tools "" -p
NIKO_STATE_DIR=niko/.runtime
NIKO_TRACE_ENABLED=1
NIKO_RUNTIME_LOG_ENABLED=1
NIKO_MEMORY_ENABLED=1
NIKO_MEMORY_RETRIEVAL_ENABLED=1
NIKO_MEMORY_GATE_ENABLED=0
NIKO_MEMORY_WRITE_ENABLED=1
NIKO_MEMORY_WRITE_GATE_ENABLED=0
NIKO_MEMORY_CORRECTION_DETECTION_ENABLED=0
NIKO_MEMORY_CORRECTION_LOOP_ENABLED=0
NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED=0
NIKO_MEMORY_CONSOLIDATE_EVERY_N_EXCHANGES=6
NIKO_MEMORY_TOP_K=4
NIKO_MEMORY_RECENT_TURNS=6
NIKO_MEMORY_RECENT_CHAR_BUDGET=2400
NIKO_MEMORY_LONG_TERM_CHAR_BUDGET=3600
NIKO_JIRA_TOOLS_ENABLED=0
NIKO_JIRA_FIXTURE_PATH=
NIKO_JIRA_LOOP_MAX_ITERATIONS=5
NIKO_JIRA_DECISION_GATE_ENABLED=0
NIKO_JIRA_DECISION_CONFIDENCE_THRESHOLD=0.75
NIKO_JIRA_DECISION_RECENT_TURNS=4
NIKO_OPS_HOST=127.0.0.1
NIKO_OPS_PORT=7777
```

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
- Keep `niko/gateway/` as a behavior-preserving runner until the app/turn
  orchestrator is explicitly introduced.
- Do not implement `NikoApp` or the turn orchestrator split without following
  `docs/plans/2026-10-08-niko-core-split-implementation-plan.md` and updating
  its checklist.
- Put Ollama/Nimble decision-model behavior in `bots/decision_model/`, not in the
  Telegram gateway. Nimble is for route/label decisions, not free-form reply
  generation.
- Put routing/agent-loop behavior in `niko/graphs/chat_reply/`.
- When adding reusable Loop mechanics, put them in `niko/loop/`. Put ToolRegistry
  adapters in `niko/tools/<domain>/`; keep actual persistence/runtime ownership
  in the domain package, such as `niko/memory/` for SQLite memory.
- Put CLI/LLM invocation details in `niko/runtime.py`.
- Put memory persistence/retrieval in `niko/memory/`.
- Put observability-only behavior in `niko/harness/` or `niko/ops/`.
- Do not recreate old entry points such as `niko/agent.py` or
  `niko/agent_router.py`; the active graph is under `niko/graphs/chat_reply/`.
- Keep `niko/.runtime/` out of git.
- Prefer small, test-covered changes. Existing tests cover routing, Telegram
  prompt behavior, memory store, trace logger, and dashboard snapshot.
