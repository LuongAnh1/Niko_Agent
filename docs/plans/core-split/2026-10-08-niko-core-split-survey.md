# Kế Hoạch Khảo Sát Tách Core, Gateway Và Graph Cho Niko

Ngày lập: 2026-10-08
Trạng thái: tài liệu khảo sát lịch sử; Phase 1/2 đã triển khai, Phase 3 đã
review lại và không giữ package orchestrator chỉ-forward

## Mục Đích

Đợt này chỉ khảo sát, đối chiếu và viết tài liệu để chuẩn bị cho lần refactor
sau. Mục tiêu là chốt ranh giới giữa gateway, core runtime, graph nghiệp vụ,
Loop và tools trước khi đụng code. Việc tách code sẽ cần một plan triển khai
riêng sau tài liệu này.

Vấn đề hiện tại: `ChatReplyGraph` đã chứng minh được baseline chạy thật, nhưng
đang bắt đầu ôm quá nhiều việc:

- route chat local/Fast/Deep;
- memory correction;
- Jira issue context workflow;
- background Deep job;
- trace/chat log/episode write;
- reply delivery callback;
- một phần điều phối workflow cấp turn.

Nếu tiếp tục thêm Jira bot, gateway khác hoặc workflow business mới trực tiếp
vào đây, graph chat sẽ thành application core ngầm và khó kiểm soát.

## 1. Đối Chiếu Với Waku

Waku có cấu trúc tách lớp rõ hơn:

- `waku/gateway/telegram.py`: gateway Telegram, chỉ xử lý polling, auth và gửi
  reply.
- `waku/gateway/runner.py`: runner cho gateway async, giữ một agent/session trên
  worker riêng và serialize turn.
- `waku/app.py`: assembly root, ráp settings, DB, tools, memory, session, tracer
  và loop.
- `waku/graph/engine.py`: graph engine generic gồm node, edge, router, state và
  observer.
- `waku/graph/workflows/triage.py`: workflow cụ thể, inject callable để test và
  render topology.
- `waku/loop/agent.py`: loop reason/tool/observe riêng, không biết gateway.
- `waku/runtime/session.py`: working memory/session per gateway conversation.

Điểm đáng học không phải là bê nguyên engine Waku, mà là cách đặt boundary:
gateway không biết workflow chi tiết; app assembly mới ráp phụ thuộc; graph là
workflow có topology; loop là runtime tool; tools không tự gửi message.

## 2. Hiện Trạng Niko

Niko đã có một số lớp đúng hướng:

- `bots/telegram/` đã tương đối mỏng: Telegram IO, auth, mention filter, `/id`,
  reply và sticker.
- `niko/chat_gateway.py` đã chuẩn hóa message thành `ChatGatewayMessage`.
- `niko/loop/` đã có Loop core V0 độc lập với Telegram.
- `niko/tools/` đã tách domain tool adapters cho memory và Jira.
- `niko/graphs/jira_issue/` đã là workflow nghiệp vụ riêng cho Jira issue context.
- `niko/memory/` vẫn sở hữu SQLite memory và guardrail mutate memory.

Điểm còn nhập nhằng tại thời điểm khảo sát:

- `ChatReplyGraph` vừa là chat workflow, vừa đang làm turn orchestrator.
- Jira workflow selection khi đó nằm trong `ChatReplyGraph` qua
  `_handle_jira_issue_prompt`; Phase 4 triển khai sau khảo sát đã chuyển phần này lên `NikoApp`.
- Memory correction selection cũng đang được gọi trực tiếp trong chat graph.
- Deep job lifecycle và reply delivery callback khiến chat graph khó tái dùng
  cho gateway khác.
- Chưa có assembly root kiểu `NikoApp` để ráp config, memory, tools, graphs và
  runtime theo một chỗ duy nhất.
- Chưa có gateway runner chung để Telegram/Jira/CLI sau này cùng gọi một
  interface xử lý turn.

## 3. Target Boundary Đề Xuất

Target sau refactor nên chia theo vai trò:

```text
bots/<gateway>/
  Platform IO: polling/webhook/auth/message shape/reply/sticker.

niko/gateway/
  Gateway runner chung: serialize turn, giữ lifecycle/session theo gateway,
  nhận ChatGatewayMessage và gọi app/core.

niko/app.py hoặc niko/agent_app.py
  Assembly root: ráp config, MemoryRuntime, TraceLogger, ToolRegistry,
  workflow registry, ChatReplyGraph, JiraIssueAnalysisWorkflow và runtime.

niko/app.py
  Turn entrypoint kiểu Waku: app có thể chọn workflow cấp turn khi Jira/memory
  selection thật sự được tách; không tạo package riêng nếu chỉ delegate.

niko/graphs/
  Workflow nghiệp vụ cụ thể: chat_reply, jira_issue, memory_correction sau này.

niko/loop/
  Tool-loop runtime generic: Tool, ToolRegistry, LoopRuntime, observer.

niko/tools/
  Tool adapters theo domain: memory, Jira, future business tools.
```

Ranh giới quan trọng:

- Gateway chỉ chuyển message vào core và nhận reply callback/event để gửi ra
  platform.
- Lớp chọn workflow cấp turn chọn workflow, nhưng không chứa logic chi tiết của workflow.
- `ChatReplyGraph` nên quay lại đúng vai trò chat workflow: local/Fast/Deep,
  busy/followup và final compose.
- `JiraIssueAnalysisWorkflow` không phụ thuộc Telegram; Jira bot thật sau này
  cũng có thể gọi cùng workflow hoặc gọi một workflow Jira khác.
- `MemoryRuntime` vẫn sở hữu memory persistence/retrieval/write/correction
  guardrail, không bị đẩy vào gateway.
- `LoopRuntime` vẫn chỉ chạy tool workflow, không trở thành application app.

## 4. Migration Strategy Cho Vòng Sau

Để tránh ôm quá rộng, nên tách theo từng commit nhỏ:

### Phase 1: Gateway Runner Khung

Mục đích: tạo lớp runner chung mà chưa đổi behavior Telegram.

- Tạo package target `niko/gateway/`.
- Định nghĩa runner nhận `ChatGatewayMessage`, `deliver_reply`, `notify_working`.
- Runner vẫn gọi `ChatReplyGraph.handle_message(...)` bên trong để giữ behavior.
- Telegram bot chuyển từ gọi graph trực tiếp sang gọi runner.
- Tests Telegram hiện tại phải pass không đổi kỳ vọng.

### Phase 2: NikoApp Assembly Root

Mục đích: gom wiring vào một chỗ thay vì global graph nằm trong gateway.

- Tạo `NikoApp` hoặc `create_niko_app()`.
- App sở hữu MemoryStore/MemoryRuntime/TraceLogger/ChatReplyGraph.
- Gateway runner nhận app instance thay vì tự biết graph.
- Dashboard/bot start path dùng app factory thống nhất.

### Phase 3: Review Boundary Theo Waku

Mục đích: kiểm tra có cần một boundary riêng trước `ChatReplyGraph` hay chưa.
Kết quả triển khai sau đó cho thấy một package orchestrator chỉ-forward không
giống tinh thần Waku bằng việc để `NikoApp` làm turn entrypoint.

- Không giữ package riêng nếu lớp đó chỉ forward.
- Giữ `NikoApp -> ChatReplyGraph` cho tới khi có Jira/memory selection thật; Phase 4
  sau đó đã bắt đầu cho `NikoApp` sở hữu selection này.
- Trace route phải giữ tên event hiện tại để dashboard không vỡ.

### Phase 4: Tách Jira Selection Khỏi Chat Graph

Mục đích: Jira không còn là nhánh riêng bên trong chat workflow.

- Chuyển logic gọi `JiraIssueAnalysisWorkflow` ra app-level workflow selection
  hoặc graph boundary thật.
- `ChatReplyGraph` chỉ nhận normal chat prompt.
- `JiraIssueAnalysisWorkflow` tiếp tục trả `JiraIssueAnalysisResult`.
- Existing Jira tests giữ nguyên behavior.

### Phase 5: Tách Memory Correction Selection

Mục đích: memory correction là workflow cấp turn, không phải phần chat reply.

- Lớp chọn workflow cấp turn gọi `MemoryRuntime.handle_memory_correction(...)`
  trước normal chat.
- Pending follow-up vẫn đi qua facade hiện tại.
- Sau khi ổn mới tính chuyện biến correction thành graph/loop workflow hoàn chỉnh.

### Phase 6: Docs, Dashboard Và Cleanup

Mục đích: docs và quan sát khớp kiến trúc mới.

- Cập nhật `AGENTS.md`, `docs/harness/architecture.md`,
  `docs/harness/telegram-chat-flow.md`, `docs/loop/architecture.md`.
- Dashboard graph semantics đổi từ `Gateway -> ChatReplyGraph` sang
  `Gateway -> GatewayRunner -> NikoApp -> Workflow`.
- Xóa hoặc giữ compatibility shim tùy mức độ rủi ro, nhưng phải ghi rõ.

## 5. Điều Không Làm Trong Đợt Khảo Sát Này

- Không tạo package `niko/gateway/` hay `NikoApp` trong đợt khảo sát này.
- Không sửa `bots/telegram/bot.py`.
- Không đổi `ChatReplyGraph`.
- Không đổi runtime behavior, dashboard API hoặc config schema.
- Không thêm Jira Cloud/API thật.
- Không tạo Jira bot/gateway thật.

## 6. Verification Cho Đợt Khảo Sát

Vì đây là docs-only:

- Chạy `rtk rg` để kiểm các cụm dễ gây hiểu nhầm như `ChatReplyGraph`,
  `tool router`, `gateway runner`, `orchestrator`, `planned`.
- Chạy `rtk git diff --check`.
- Không bắt buộc chạy full pytest nếu chỉ đổi Markdown/AGENTS.

Acceptance criteria:

- Có plan và checklist riêng trong `docs/plans/`.
- Docs nói rõ refactor core/gateway/graph là target tương lai, chưa triển khai.
- Tài liệu không làm người đọc hiểu nhầm rằng Niko đã có gateway runner hoặc app
  orchestrator chung.
- Checklist đủ chi tiết để vòng sau lên plan triển khai code theo phase nhỏ.
