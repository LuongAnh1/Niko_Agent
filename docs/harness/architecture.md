# Kiến Trúc Niko Agent

Niko Agent là một harness local cho AI agent. Mục tiêu của repo là có một hệ thống chạy thật: nhận prompt từ Telegram, định tuyến qua Fast/Deep agent, inject memory context cho Deep agent, ghi trace, lưu memory cơ bản và quan sát được trên dashboard.

## Mục Tiêu Hiện Tại

- Có Telegram gateway mỏng để nhận/gửi message.
- Có khung điều phối agent độc lập với gateway.
- Có hai chế độ xử lý:
  - `single`: đẩy thẳng vào Deep agent.
  - `two_agent`: dùng Fast agent để trả lời nhanh/triage, Deep agent để xử lý tác vụ cần phân tích.
- Có baseline memory gồm `chat_log`, `facts`, `episodes`.
- Có trace JSONL để dashboard và báo cáo có dữ liệu thực tế.
- Có Mini Niko Ops dashboard để nhìn luồng harness và quản lý facts.

## Kiến Trúc Tổng Quan

Trang này mô tả ranh giới module và trạng thái baseline. Bản đồ theo từng lượt
chat đang chạy nằm ở [Luồng runtime hiện tại](current-runtime-flow.md).

```text
Telegram
  -> bots/telegram
  -> ChatGatewayMessage
  -> GatewayRunner
  -> NikoApp
  -> ChatReplyGraph
     -> local rule
     -> Ollama/Nimble decision triage
     -> Fast Agent reply/final
     -> Deep Agent background
        -> Memory retrieval
        -> fcc-claude local runtime
  -> Telegram reply

Harness side effects:
  -> JSONL trace
  -> SQLite chat_log / facts / episodes
  -> Mini Niko Ops dashboard
```

Dashboard đang biểu diễn các khối chính:

```text
Gateway -> GatewayRunner -> NikoApp -> Fast Agent -> Reply
                              \-> Memory Gate -> Loop/Deep Agent -> Reply

Memory Gate -> Memory Records
Reply/turn events -> Trace/Ops
```

Trong đó `Tool Slot`/`Loop` hiện là cơ chế V0 cho memory correction và Jira
issue flow, chưa phải tool router hoàn chỉnh cho toàn bộ chat flow hoặc Jira
domain. Thiết kế Loop tổng quát nằm ở `docs/loop/architecture.md`.

## Bố Cục Repo

```text
bots/
  decision_model/
    client.py          # Ollama /v1/systemone client cho Nimble decision model
    triage.py          # Mapping prompt Telegram sang reply_now/send_to_deep
    sticker.py         # Mapping prompt/reply sang mood sticker Telegram
    memory/            # Retrieval/write/candidate/correction decisions cho chat memory
    warmup.py          # Giữ Nimble loaded với keep_alive=-1
  telegram/
    bot.py             # Telegram gateway: polling, auth, mention filter, /id, reply, sticker
    instance_guard.py  # Single-instance lock/PID guard cho Telegram long polling
    sticker_picker.py  # Chọn file_id theo mood sticker đã quyết định
    stickers/
      ducks.json       # Mapping mood sang sticker Telegram

niko/
  runtime.py           # Gọi Claude CLI, đọc hook, build prompt, inject memory context
  config.py            # Load bootstrap env, runtime config và resolve project path
  chat_gateway.py      # ChatGatewayMessage, identity, alias, allowed user parsing
  app.py               # Assembly root mỏng, sở hữu ChatReplyGraph hiện tại
  gateway/
    runner.py          # Runner mỏng nối gateway vào NikoApp hiện tại
  HOOK.md              # Persona/hook nạp vào Niko
  harness/
    trace.py           # JSONL turn/event tracing
    runtime_log.py     # JSONL runtime log cho tab Bots
  loop/
    types.py           # Tool/ToolContext/ToolResult/LoopDecision/LoopResult
    registry.py        # ToolRegistry execute tool an toàn
    runtime.py         # LoopRuntime V0 với max iteration và fallback
    observer.py        # Noop/Trace observer cho loop events
  memory/
    store.py           # SQLite store: chat_log, facts, episodes, FTS/fallback search
    runtime.py         # MemoryRuntime điều phối retrieval/write/correction, consolidation và format context
    context.py         # Dataclass, formatter và wrapper tương thích
    working_memory.py  # Recent conversation window tạm thời cho Deep/correction
    correction_workflow.py # Workflow sửa/xóa fact qua chat
    correction_loop.py # Bridge correction default-off qua LoopRuntime + fact tools
    consolidation.py   # Scaffold đọc/mark batch chat_log chưa consolidated
  tools/
    memory/facts.py    # search/list/update/delete fact tools cho Loop core V0
    jira/issues.py     # read-only Jira fixture tools V0
  ops/
    dashboard.py       # HTTP server/entrypoint mỏng cho Niko Ops dashboard
    bots.py            # Start/stop Telegram bot, warmup/stop Decision Model
    config_schema.py   # Schema, validate, snapshot cho dashboard Config
    frontend.py        # Load HTML template
    templates/
      dashboard.html   # HTML/CSS/JS của dashboard
  graphs/
    chat_reply/
      graph.py         # ChatReplyGraph điều phối flow chat
      router.py        # Rule router local/deep/fast/busy
      prompts.py       # Prompt task cho Fast Agent và final compose
    jira_issue/
      workflow.py      # Jira issue context flow V0 qua Loop tools
```

## Ranh Giới Trách Nhiệm

`bots/telegram` là gateway. Nó chỉ nên biết Telegram API, message shape, mention filter, `/id`, auth, send message và send sticker. Gateway không quyết định agent nào xử lý và không chứa logic memory.

`niko.chat_gateway` chuẩn hóa input từ gateway thành `ChatGatewayMessage`. Nếu sau này thêm Zalo/Discord/CLI, gateway mới nên convert message về cùng abstraction này.

`niko.gateway` là runner chung cho gateway. Runner nhận message/callback đã
chuẩn hóa rồi chuyển nguyên sang `NikoApp`, chưa chọn workflow và chưa thay đổi
route/trace/log hiện có.

`niko.app` là assembly root theo tinh thần Waku. `NikoApp` hiện sở hữu
`ChatReplyGraph`, có chỗ inject `MemoryStore`/`MemoryRuntime`/`TraceLogger`, mở
turn, giữ thứ tự route/busy, chọn memory correction và Jira issue workflow, rồi
chuyển normal local/Fast/Deep chat sang chat graph. Repo đã từng thử thêm một
package orchestrator mỏng, nhưng đã gỡ vì nó chỉ forward và làm khác cấu trúc
Waku mà chưa đem lại workflow selection thật.

`niko.graphs.chat_reply` là graph nghiệp vụ chat. Nó giữ route local/fast/deep/busy,
quản lý deep job background, ghi trace, ghi chat log và episode sau deep job.
Jira-specific selection không còn nằm trong graph này.

`niko.graphs.jira_issue` là workflow nghiệp vụ Jira V0. Khi dashboard bật
`NIKO_JIRA_TOOLS_ENABLED=1`, prompt có issue key được đưa qua Loop để fetch fixture
issue/comment/changelog, format context có evidence, rồi mới handoff sang Deep.
Workflow này không phải Jira bot/gateway và không ghi dữ liệu Jira vào chat memory
SQLite mặc định. Khi bật thêm `NIKO_JIRA_DECISION_GATE_ENABLED=1`, Nimble chỉ
phân loại prompt mơ hồ; Python vẫn parse/validate issue key và gọi tool thật.

`niko.runtime` là lớp gọi Claude CLI. Nó đọc env command, resolve `CLAUDE_WORKDIR`, nạp `niko/HOOK.md`, chèn identity context, gọi `MemoryRuntime` để lấy memory context rồi gọi `fcc-claude`.

`niko.memory` là memory baseline. `MemoryRuntime` là cổng điều phối retrieval
gate, retrieval modes (`search/list/recent/none`), recent working memory, write
gate, correction facade, manual/auto consolidation và format context cho Deep.
`MemoryCorrectionWorkflow` giữ durable pending state và mutate guardrail cho
Phase 5 V1.
`MemoryCorrectionLoopWorkflow` là bridge Phase 4A default-off: prompt correction
trực tiếp có thể chạy qua LoopRuntime + fact tools khi bật
`NIKO_MEMORY_CORRECTION_LOOP_ENABLED=1`, còn lỗi loop fallback về V1.
Store hiện dùng SQLite local, có FTS5 nếu môi trường SQLite hỗ trợ và fallback
search nếu không có FTS5.

Consolidation có đường manual qua dashboard/API (`Refresh batch`, `Run once`) và
đường auto default-off sau complete exchange. Auto chỉ chạy khi bật
`NIKO_MEMORY_CONSOLIDATION_AUTO_ENABLED=1`.

`niko.harness` và `niko.ops` là lớp quan sát/vận hành. Dashboard đọc memory/trace, không tham gia trực tiếp vào agent loop.

Loop core V0 là lớp tool workflow độc lập với Telegram. Tool adapters nằm dưới
`niko/tools/`: fact tools ở `niko/tools/memory/facts.py`, Jira fixture tools ở
`niko/tools/jira/issues.py`. Memory correction có đường thử nghiệm default-off
để gọi Loop sau correction gate và trước nhánh V1 search/apply; Jira issue flow
V0 dùng cùng LoopRuntime để đưa context business sang Deep. Tool
không tự gửi reply Telegram và mọi mutate phải đi qua guardrail Python có trace.
Phase đầu dùng Python-controlled loop vì runtime hiện gọi Claude qua
`fcc-claude` CLI, chưa có native tool-use API ổn định trong application code.

## Hướng Tách Core/Gateway/Graph

Sau khi thêm memory correction loop và Jira issue workflow, repo đã bắt đầu tách
lựa chọn workflow cấp turn ra khỏi `ChatReplyGraph`: Phase 4 đưa Jira selection
lên `NikoApp`, Phase 5 khóa memory correction selection ở `NikoApp`. Target refactor đã
được khảo sát trong `docs/plans/core-split/2026-10-08-niko-core-split-survey.md`: giữ
`bots/<gateway>/` cho platform IO, thêm gateway runner chung, thêm app assembly
root để ráp memory/tools/graphs/runtime, rồi đưa lựa chọn workflow cấp turn ra
khỏi `ChatReplyGraph` khi có logic thật sự cần tách.

Phương hướng triển khai theo phase nằm ở
`docs/plans/core-split/2026-10-08-niko-core-split-implementation-plan.md`; checklist để tick
từng phần nằm ở `docs/plans/core-split/2026-10-08-niko-core-split-implementation-checklist.md`.

Trạng thái hiện tại: Phase 1 đã có package `niko/gateway/` với `GatewayRunner`
mỏng. Phase 2 đã có `niko/app.py` với `NikoApp` assembly root. Phase 3 được
điều chỉnh lại sau review với Waku: không giữ package orchestrator chỉ forward.
Phase 4 đưa Jira workflow selection lên `NikoApp`; Phase 5 khóa memory correction
như workflow cấp turn bằng regression tests. Telegram gateway gọi runner, runner
gọi app, app chọn workflow cấp turn rồi mới đưa normal chat sang `ChatReplyGraph`.

## Import Chính

```python
from niko.gateway import GatewayRunner
```

Telegram gateway đang tạo một instance global:

```python
GATEWAY_RUNNER = GatewayRunner()
```

## Config Chính

Luồng vận hành chính là dashboard-first:

1. Chạy `python -m niko.ops.dashboard`.
2. Chỉnh cấu hình trong tab Config.
3. Start Telegram bot và warmup/stop Decision Model trong tab Bots.

Các nhóm config chính trong dashboard:

- `Telegram Gateway`: token, allowlist, group mode, timeout/retry.
- `Agent Commands`: Claude/Fast command, workdir, timeout, hook file, identity.
- `Decision Model`: Ollama/Nimble base URL, model, triage/warmup/stop timeout, keep alive.
- `Sticker`: bật/tắt sticker, sticker set/config/mode, timeout.
- `Memory & Trace`: memory, recent context budget, retrieval/write/correction gate, trace, runtime log.
- `Business Tools`: bật/tắt Jira tools, fixture path, max iteration cho Jira Loop
  và Jira Decision Gate cho prompt mơ hồ.
- `Replies`: suffix, wait/busy/error text.

Tab Config trong dashboard ghi runtime override vào `niko/.runtime/config.json`.
Khi cần thao tác nhanh, có thể sửa trực tiếp file JSON này rồi refresh dashboard;
với key được đọc lúc process start như token, allowlist hoặc agent command thì
restart Telegram Bot trong tab Bots.
Niko không còn dùng `niko/.env` hoặc `bots/telegram/.env`; chỉ root `.env` còn
vai trò bootstrap dashboard (`NIKO_OPS_HOST`, `NIKO_OPS_PORT`,
`NIKO_RUNTIME_CONFIG_FILE`). Thứ tự cấu hình hiệu lực là: OS env thật -> runtime
config -> root `.env` bootstrap -> default trong code. Secret/token có thể lưu
trong runtime config local; dashboard mask secret trong snapshot/API.

## Dữ Liệu Runtime

```text
niko/.runtime/
  config.json                      # Runtime config do dashboard Config ghi
  niko_memory.sqlite3              # SQLite memory
  traces/YYYY-MM-DD.jsonl          # JSONL trace
  logs/YYYY-MM-DD.jsonl            # Runtime log cho tab Bots
  claude_sandbox/                  # CLAUDE_WORKDIR
```

Thư mục `niko/.runtime/` là dữ liệu local, không commit. Nếu cần reset demo thì có thể dừng bot/dashboard rồi xóa hoặc backup các file runtime liên quan.

## Trạng Thái Baseline

Đã có:

- Gateway Telegram chạy thật.
- GatewayRunner/NikoApp bọc đường gọi Telegram; `NikoApp` chọn memory correction/Jira
  workflow trước khi đưa normal chat sang `ChatReplyGraph`.
- Fast/Deep agent flow.
- Memory retrieval cho Deep agent.
- Recent working-memory window cho Deep prompt theo `conversation_id`.
- Retrieval/write gate bằng local Decision Model, gồm mode inventory qua
  `fact_mode`/`episode_mode`.
- Memory correction V1 qua chat: nhận diện sửa/xóa fact, hỏi lại khi mơ hồ và
  update/delete SQLite có trace. Pending ambiguous fact-ID được lưu trong SQLite
  với TTL 15 phút. Đây là lớp tạm trước khi có Loop/tool workflow đầy đủ.
- Memory correction Loop V0 default-off: dùng LoopRuntime + fact tools cho prompt
  correction trực tiếp, fallback về V1 khi loop lỗi.
- Jira issue context flow V0 default-off: prompt có issue key fetch fixture qua
  Loop tools, format evidence context và đưa sang Deep khi bật `NIKO_JIRA_TOOLS_ENABLED=1`.
- Jira Decision Gate V0 default-off: dùng Nimble cho prompt Jira mơ hồ, nhưng
  fail-safe về route cũ khi lỗi hoặc confidence thấp.
- Manual semantic facts qua dashboard.
- Episodic record sau deep job và consolidation batch từ `chat_log`.
- JSONL trace và Mini Ops dashboard.

Chưa có:

- Tool router hoàn chỉnh nối vào chat flow.
- Tool router hoàn chỉnh sở hữu toàn bộ ambiguous/pending correction workflow.
- Jira bot/gateway thật và kết nối Jira Cloud/API production.
- Automatic semantic extraction đáng tin cậy từ mọi đoạn chat.
- Embedding/rerank/graph reasoning.
- Lakehouse/Knowledge Graph production layer cho Jira/business data.

## Hướng Mở Rộng

- Thêm gateway mới trong hiện trạng: tạo folder trong `bots/`, parse message về `ChatGatewayMessage`, rồi gọi `GatewayRunner`.
- Thêm gateway mới sau refactor core/gateway/graph: gateway nên gọi runner/app chung thay vì gọi `ChatReplyGraph` trực tiếp.
- Thêm nghiệp vụ mới: tạo graph mới trong `niko/graphs/`.
- Thêm tool/loop: dùng `Tool Slot` hiện có như điểm mở rộng, nhưng giữ Telegram gateway mỏng.
- Khi thêm tool mới cho Loop, đặt adapter dưới `niko/tools/<domain>/`; graph mới
  vẫn đặt dưới `niko/graphs/`, còn store/runtime dữ liệu ở domain sở hữu thật.
- Cải tiến chat memory: làm chắc retrieval/write gate, consolidation và correction trên SQLite local trước.
- Cải tiến memory nghiệp vụ: nối sang lane lakehouse/Jira qua retrieval/tool slot khi cần dữ liệu issue/tài liệu, không trộn vào chat memory v1.
