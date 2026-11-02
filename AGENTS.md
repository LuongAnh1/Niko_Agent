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

The Niko SDLC plan index is documented in `docs/plans/sdlc/README.md`. The
current Phase 4 source of truth is
`docs/plans/sdlc/2026-10-15/phase4-integration-plan.md` and its checklist.

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

## Mandatory Harness / Validator Boundary

Keep deterministic Harness validation separate from semantic Design validation.
This is a mandatory architecture rule for future SDLC work, not a UC1-specific
lesson.

- The Harness may hard-reject only mechanically decidable invariants: schema and
  types, stable IDs, valid endpoints, declared field mappings, runtime/repository
  bindings, boundary compatibility, reachability, sequence continuity, and one
  final observable terminal interaction where required.
- The semantic Validator owns judgments that require interpreting Requirement or
  Design meaning: whether a decision has enough information, whether a topology
  is reasonable for the scenario, authentication/authorization correctness,
  negative constraints, transaction meaning, focus/announcement behavior, and
  whether outcomes actually satisfy the approved Requirement.
- Use this classification test before adding a gate: if the verdict can be
  derived from typed artifacts without interpreting domain prose, it can be a
  Harness rule. If it depends on what a condition, field, component, actor or
  outcome means in context, it belongs in semantic validation.
- Topology is split deliberately: the Harness checks that a path is connected
  and executable; the Validator decides whether that path is the right design.
- Never repair a semantic failure by adding use-case keywords, fuzzy component
  names, prescribed field names, status/live-region name matching, or a fixed
  `UI -> API -> business_rule -> data` chain to a deterministic gate.
- Semantic findings must cite stable Requirement and Design IDs. Provider output
  that is empty, truncated or unparsable must fail closed and can never create a
  checkpoint.

## Session Handoff: SDLC_Harness L3.10 (as of 2026-11-02, after controlled run r9 checkpoint)

Active work is in the sibling repo `../SDLC_Harness`, not in Niko. Current
focus is **Requirement + Design only**. The r8 Harness hardening is committed
and pushed as SDLC_Harness `7357bf3` on `main` (base was `3d66250`). Pre-r9
review/parser fixes after that commit are local and uncommitted. Commit later
work only when the owner asks; never stage `docs/` or `.runtime/`.

Read first (local-only docs in `../SDLC_Harness/docs/`):
`role-specific-agent-loop-upgrade/l3.10-holistic-ui-ux-design-implementation-plan.md`,
`progress.md`, `implementation-plan.md`, and
`workflow-reference/phase-2-design/README.md`. The Phase 2 workflow reference
was audited and expanded this session: `README.md` and
`designer-runtime-reference.md` were added, while
`phase-2-design-workflow.md`, `vertical-design-graph.md`,
`design-validation-and-repair-policy.md`, and
`vertical-slice-trace-graph.md` were updated. These files remain local-only.

### Where things stand

- Controlled run r9 `run_20261102_100240_755e94d0` (runtime root
  `.runtime/l3.10-controlled-20261102-r9`, eTour UC1, same approved Requirement
  V2 as r8) reached `waiting_design_approval` at graph revision 8: 12 nodes,
  18 edges, 7 partitions, all 10 required surfaces covered, 2 valid branch
  parents, vertical trace 0 blockers / 0 warnings, semantic quality 0 blockers /
  1 warning. **Do not approve**: approval automatically moves the run to Plan.
  Do not start another r9.
- Final artifact to show the owner is
  `artifacts/ui_feature_experience_v2.md`. It has two external runtime inputs
  (`agencyOperatorSession` from PRE-1 and `culturalHeritageRecords` from MF-1,
  both provided by `Root`), separate SC-1/SC-2/SC-3 journeys, the SC-3 alert
  inside the dialog overlay, and focus on Retry rather than the announced alert.
  The one warning is a real but non-blocking ambiguity about when the Cancel
  action stops being available on the shared confirmation dialog.
- Earlier r9 history: `designer-0005` contradicted AC-4 by disabling controls on
  deletion confirmation and moved focus to a status announcement. The Harness
  was hardened, that pending step was neutrally rejected, `designer-0006`
  returned invalid JSON which the driver caught, `designer-0007` produced the UI
  delta that became graph revision 1, and `designer-0008` read backend source.
- `designer-0009` is valid JSON but unsafe Design: it says the agency is already
  authenticated while setting `authorization_required=false`, invents an
  "existing session" without repository evidence, omits the UI -> API and final
  API -> UI path, and reverses the provider/consumer direction of one returns
  contract. It has not been applied. The graph from `designer-0007` also exposes
  the earlier Harness defect that it inserted Root and AppShell wiring nodes but
  did not mechanically insert the Root -> AppShell calls edge.
- After the auth/topology/wiring fix, `designer-0010` correctly created the SC-1
  API/rule path and the Harness reconciled Root -> AppShell (revision 2);
  `designer-0013` inserted persistence into the path (revision 3). SC-2 output
  `designer-0014` put `visual_contract` at the wrong level; the existing graph
  gate rejected it before mutation and `designer-0015` corrected it (revision
  4). `designer-0016` added the valid SC-3 UI interruption projection (revision
  5); `designer-0017` read API source.
- `designer-0018` exposed a semantic question, not a safe deterministic
  gate: its SC-3 rollback rule names the ETOUR interruption but declares only
  `culturalHeritageId` and `confirmation`. That may be missing data, or it may be
  valid when an already-selected error/catch path invokes the interruption
  handler. Requiring a fixed `connectionInterrupted` field would reject valid
  designs. The final semantic Design Validator now owns this decision and must
  block when neither an input/boundary mapping, response/error event,
  already-selected control-flow path nor local state supplies the condition.
  It was structurally approved as revision 6; `designer-0019` added the SC-3
  rollback persistence projection as revision 7. The semantic Validator later
  confirmed that the already-selected interruption path supplies the condition.
- Deterministic validation then correctly caught two SC-3 sequence-order defects:
  the UI return was terminal before the persistence messages, and orders 5/6
  jumped to unrelated participants. Repair `design_v2/designer-0002` reordered
  the path to UI -> API -> rule -> data -> rule -> API -> UI and produced graph
  revision 8.
- Semantic exchange `design-validator-0001-a01` consumed all 32,768 completion
  tokens as reasoning (`finish_reason=length`) and returned no JSON. It was not
  approved. The Harness now gives the semantic Validator 65,536 tokens and a
  600-second timeout by default, and unattended execution raises a dedicated
  `design_semantic_review_truncated` error for `finish_reason=length` plus a
  fail-closed error for empty output. Neutral retry `a02` returned complete JSON:
  all 25 canonical claims assessed, 0 blockers, 1 warning.
- r9 proves the repaired path can reach a correct checkpoint, but it is not an
  unattended-stability proof: the run included controlled per-call approvals,
  neutral regeneration after Harness fixes, one structural repair iteration and
  one rejected provider-truncated Validator output. The next meaningful proof is
  a differently shaped use case, not another UC1 rerun.
- Controlled run r8 `run_20261102_061023_4ed5c466` (runtime root
  `.runtime/l3.10-controlled-20261102-r8`, eTour UC1, approved Requirement from
  Phase 1 run r7) is paused at `waiting_design_approval`. **Do not approve**:
  approval automatically moves the run on to Plan.
- r8 result: vertical trace validation 0 blocking / 0 warnings, 10/10 required
  surfaces, 2 valid branch parents, 14 requirements traced, 12 nodes / 16 edges,
  pin `layered_layout_v1`. The layered layout is correct (the SC-3
  connection-interrupted alert is in the dialog `overlay`), Runtime inputs show
  2 entries (`agencySession` PRE-1, `culturalHeritageList` MF-1, both via
  `Root`), and each scenario has its own journey and focus policies.
- r8 is **not** proof of unattended stability: it stopped 8 times, the Harness
  was fixed 6 times mid-run (the stopped step was then rejected with a neutral
  "Harness corrected X, regenerate" note), there were 3 manual reject notes and
  2 approvals past a known model error. r5/r6/r7 each failed differently at
  designer-0004, so DeepSeek varies between runs.
- The owner deleted runtime roots r1-r4 (2026-11-01) and r5-r6; r4 is no longer
  available as a reference run. r7 is kept (stopped at designer-0004).

### Harness changes in `7357bf3` (general across use cases)

- Layered layout contract and projection fixes from the morning of 2026-11-02
  (per-scenario runtime states, merged layout regions, overlay wireframe,
  cross-node layout/style checks); defaults partition action budget 12 and
  investigation timeout 300s.
- **Harness owns composition wiring** (`_materialize_delta_wiring`,
  `execution.py`): the Designer declares inputs only on the screen node; the
  Harness inserts the filled `composition_wiring` template for every AS-IS hop,
  replaces Designer-written wiring with the template (keeping its temp_ref),
  and moves every `external_use_case` `provided_by` to the entrypoint
  (`_normalize_external_providers`). Prompt key is
  `active_partition.harness_wiring_operations`.
- **Harness owns the branch parent edge** (`_materialize_branch_parent`):
  inserts the seeded branch->main `depends_on` when the first branch delta
  omits it; `branch_parent_missing/invalid` repairs may target the main flow
  entry. `_main_flow_entry_node` skips wiring and prefers the source of the
  first ordered behavior edge.
- Gates (`graph.py`): boundary contract checks also run on same-component
  edges that declare a contract (this is what caught the reversed r8 returns
  edge); every `business_rule` input must be a required `input_mapping` target;
  an API with only 4xx/5xx responses needs no `response_mapping`.
- Context/prompt: `feature_ux_decisions` lists focus policies per owning
  scenario (`scenario_focus_policies`) and ignores wiring nodes; the operator
  rejected delta is shown in the next prompt (`last_review_rejected_delta`);
  prompt contradictions removed (wiring ownership, edge endpoints may be
  same-delta temp_refs, required props apply to screen nodes, provided_by
  placeholder names the entrypoint).
- Loop robustness (Codex audit EXE-002/007/009/010): repeated-action
  fingerprint uses the Designer's own arguments; `complete` is rejected while a
  partition or repair task is active (code `active_work_pending`); setup errors
  go through `_execution_error` / `DesignerExecutionError`;
  `designer_repo_path_unsafe` is a rejected action.
- Pre-r9 offline hardening fixed the step-review driver's confusion between
  `parsed_output` and invalid JSON, and canonicalizes external input provider plus
  dependency `requirement_refs` before Codex reviews the parsed action.
- The r9 audit moved context-dependent approval policy out of deterministic
  Designer gates and into the semantic Design Validator. Removed hard-coded
  keyword inference for authenticated preconditions, the UC1-specific
  deletion-confirmation prohibited effect, status/live-region name matching,
  the fixed `UI -> API -> business_rule -> ... -> UI` topology obligation, and
  the assumption that a data surface must be inserted immediately after a
  business-rule node. Prompt guidance may still help the Designer, but it is not
  an approval rule.
- The prompt audit after `designer-0009` found three general Harness causes:
  approved preconditions were absent from the active Requirement slice; the API
  scaffold prefilled `authorization_required=false` and conflated authentication
  with role authorization; and the Harness supplied wiring nodes but no wiring
  edges. The backend prompt also
  carried the complete unrelated UI experience contract (about 18K of a 45K
  prompt). Requirement V2 was deliberately not changed: Requirement still owns
  elicitation and clarification, while Design owns implementation decisions.
- Phase 2 now projects approved preconditions into local
  `cross_cutting_conditions`. New API contracts separate `authentication` from
  `authorization`, cite Requirement refs, distinguish
  `existing_runtime|external_dependency|new_boundary|none`, and require frozen
  repository evidence before claiming existing authentication. The scaffold no
  longer decides auth from English keywords. Graph validation preserves legacy
  contracts, but when the typed access fields are present it enforces their
  types, source/evidence consistency, and a boolean `authorization_required`;
  the semantic Validator decides whether those values satisfy the approved
  Requirement. Boundary `auth_mapping` remains required when either typed
  authentication or authorization is required.
- Deterministic topology validation remains general: required surfaces must be
  reachable from a runtime entrypoint, cross-application calls need an executable
  boundary, declared mappings must match provider contracts, and each UI
  scenario has one continuous observable terminal path. The Harness no longer
  prescribes one surface ordering or reverse-return chain. Composition
  materialization still inserts missing
  nonterminal calls edges between consecutive Harness-owned AS-IS wiring hops,
  including reconciliation of the existing r9 Root -> AppShell gap.
- Backend construction prompts retain graph topology and contracts but strip
  unrelated UI `experience_contract` payload. Prompt text now states that the
  Harness owns wiring nodes plus internal wiring edges, the Designer owns only
  the semantic final hop, and returns edges run provider -> consumer.
- The Design semantic review policy now checks decision/guard input sufficiency,
  negative constraints, authentication versus authorization, focus/announcement
  behavior, and runtime topology without prescribing a field name or one fixed
  architecture. Its blocker loop already feeds exact node/edge findings back to
  Designer and cannot create the Design checkpoint until blockers are cleared.
- The Design semantic profile now defaults to 65,536 max tokens / 600 seconds.
  `_complete_json` explicitly rejects token-truncated or empty provider output;
  this prevents a reasoning-only DeepSeek completion from being mistaken for a
  semantic verdict in unattended runs.
- Regression coverage includes the semantic policy prompt, cross-cutting
  preconditions as raw context, typed auth contract structure, removal of
  prescribed topology/data insertion, automatic wiring-edge materialization,
  final reachability gates, and backend prompt compaction. Focused
  Designer/Validator tests passed **129/129**; the post-validator-budget Phase 2,
  semantic-validator and runtime-config set passed **82/82**; last full suite:
  **973 passed, 1 skipped**. The prior known flaky
  `test_write_api::test_write_validation_auth_and_missing_operation_errors_are_json`
  did not fail in this run; its earlier WinError 10053 root cause remains unknown.

### Review workflow (agreed with the owner)

- As of pending `designer-0014`, the owner asked the lead agent to run and check
  every input/output directly instead of letting Codex CLI auto-drive the loop.
  Use `scripts/run_l3_designer_shadow.py` one review decision at a time, inspect
  the exact prompt, parsed action, post-approval Harness result and graph revision,
  then stop at the next pending call. Codex findings are advisory history, not
  the decision maker; do not resume `scripts/codex_step_review.py` unattended.
- On every blocking stop, only read, verify and analyze, then report with a
  recommendation; no Harness code edits, approve/reject or resume until the owner
  decides. Challenge model output and deterministic gates independently.
- **Judge every stop as if the Harness ran unattended** (owner, 2026-11-02):
  in production nobody rejects manually. If the Harness itself would catch the
  error and feed it back to DeepSeek, approve and let the run prove it. If it
  cannot (missing gate, misleading context, contract conflict), that is a
  Harness defect: fix the Harness generally. Do not use manual reject notes as
  the fix; a neutral reject is only for regenerating a step after a Harness fix.
- Before trusting a gate, verify it on the real call path (the r8 lesson: a
  direct `_boundary_contract_diagnostics` call passed while the real path skipped
  same-component edges).
- At the Design checkpoint, show the owner `ui_feature_experience_v<N>.md`; do
  not approve unless the owner asks.

### Next steps

1. Show the owner `ui_feature_experience_v2.md` and the one semantic warning.
   Never approve the r9 Design checkpoint without the owner's request.
2. Run a second use case with a different shape (several screens, no UI, or
   API-only) to check the fixes are general.
3. Keep the r9 runtime as evidence; do not resume it into Plan merely to test the
   driver. The driver process loads code at start, so any future resumed process
   must load the current Harness code.

### Open items

1. EXE-004: a hop already wired for input A is treated as done for input B, in
   both `_wiring_hop_inputs` and the validator shortcut (`graph.py`
   `composition_hop_unmodeled`). Right fix: canonical input names per
   `screen_id`; merge with item 2.
2. Known gap: merged screen text repeats near-duplicate clauses (layout,
   typography, responsive, wireframe, component tree per scenario) because each
   scenario node rewrites the whole screen contract. Bigger option: one
   screen-owner node plus per-scenario state/transition deltas.
3. Codex audit leftovers: EXE-011 (crash recovery does not mark repair targets
   completed), EXE-012 (wiring identity uses the symbol only), EXE-013..015
   (`completion_claims` unused, `ui_guidance_required` written but never read,
   `_repair_targets` dead code; `complete` is now effectively dead too).
   EXE-008 is resolved for new construction and repair deltas by the explicit
   authn/authz contract; historical graphs remain readable.
4. Flaky `test_write_api` test above; root cause not investigated.

Resolved before r9: external dependency bindings are copied into
`requirement_refs` before step review; the valid structured output from
`design-validator-0001-a01` was misclassified by the driver rather than unparsable,
and the driver now recognizes `parsed_output`; `progress.md` and the L3.10 checklist
now record r5-r8 plus the pre-r9 2026-11-02 hardening. All r9 code and docs work
described above remains local and uncommitted; the current runtime is paused at
`waiting_design_approval`; commit only when the owner asks.

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
- `niko/sdlc/`: HTTP API client từ Niko sang repo `../SDLC_Harness`; Niko chỉ gửi
  action và nhận JSON trạng thái, không tự kéo use case từ Jira.
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
user gives a clear run id. Phase 1/2/3 integration is approve-only: Niko can
report requirement, design, and implementation-plan checkpoints, but does not
edit SDLC artifacts or handle change requests yet. Keep this boundary strict:

```text
Telegram/user prompt
  -> Niko detects an SDLC intent
  -> Niko calls the SDLC_Harness HTTP API through one client facade
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

Current Telegram SDLC actions are `start`, `get_status`, Phase 1 V2
`answer_clarification`/`clarify_more`/`challenge_assumption`/deterministic-only
`revalidate_requirement`,
Phase 1/2/3 `approve_checkpoint`, Phase 4 `continue_run`/`sync_run`, Phase 2
`revalidate_design`, and gated Code Review approval. Control actions use
an explicit `run_id`; clarification answers also include the current question
ID so stale replies cannot answer a later question. Long-running write actions
run in the background and return queued plus final replies. Code Review approval requires
`NIKO_SDLC_CODE_REVIEW_APPROVAL_ENABLED=1`, whose safe default is `0`, and may
only request Draft -> Ready for review. Niko should surface SDLC
`blocked`/sync-partial states clearly by reading `last_error` and `last_sync`;
approval sync failure must retry `sync_run` instead of approving GitHub again.
Slice 8 cutover passed with live run `run_20261019_034156_05cfbb93` on
2026-10-19. Niko is API-only for SDLC integration; its subprocess transport,
transport selector and CLI-specific config were removed. HTTP writes use durable
operations, idempotency and polling. After an ambiguous submit or timeout, Niko
must not create a new operation through another path because that can execute
the workflow twice. The standalone SDLC_Harness CLI remains an operator/debug tool.
`revalidate_design` rereads the frozen Requirement V2 and the latest Phase 2
design, then runs the hybrid V2 validator in a bounded current-version repair
loop. Each version receives deterministic traceability/parser/cross-diagram
checks plus one semantic review; only blockers feed the next Designer repair.
Warnings do not block approval, zero blockers create the checkpoint, and a
repeated blocker set stops the loop as stalled.

Do not expose `request_change`, `cancel_run`, `resume_run`, dynamic context
invalidation, merge, or auto-merge until the matching SDLC_Harness phase and
JSON contract exist. SDLC_Harness owns worktree-per-run identity and locking.
Niko never checks out target-repo branches, manages worktrees, merges PRs, or
enables auto-merge; the project owner always performs merge on GitHub.

Niko owns the local switch `NIKO_SDLC_SYNC_MODE`, whose safe default is `mock`,
and sends it explicitly in API requests; use `real` only for an intentional
Jira/Confluence live run. SDLC_Harness owns its LLM and server runtime config.
Phase 4 intent, formatter, background runner, integration fixture and controlled
live smoke have passed. Keep approval default-off and require an intentional
live run before enabling it.
<!-- NIKO_SDLC_SYNC_END -->

