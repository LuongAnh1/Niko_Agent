# Checklist Khảo Sát Tách Core, Gateway Và Graph Cho Niko

Ngày lập: 2026-10-08
Tài liệu gốc: `docs/plans/2026-10-08-niko-core-split-survey.md`
Phạm vi: khảo sát, đối chiếu, suy luận và cập nhật tài liệu; chưa refactor code.

Ghi chú sau triển khai 2026-10-08: đề xuất `niko/orchestration/` đã được review
lại theo Waku và không giữ trong code vì bản V0 chỉ forward. Hướng hiện tại là
`GatewayRunner -> NikoApp -> ChatReplyGraph` cho tới khi có workflow selection
thật sự cần tách.

## Phase 1: Khảo Sát Waku

Mục đích: hiểu cách Waku tách gateway, runner, app assembly, graph, loop và tools
để lấy ý tưởng boundary, không bê nguyên implementation.

- [x] Rà `waku/gateway/telegram.py` để xác định gateway chỉ làm IO/auth/reply.
- [x] Rà `waku/gateway/runner.py` để ghi nhận mô hình runner serialize turn.
- [x] Rà `waku/app.py` để ghi nhận assembly root ráp settings, DB, memory, tools,
      session, tracer và loop.
- [x] Rà `waku/graph/engine.py` và `waku/graph/workflows/triage.py` để ghi nhận
      graph engine generic và workflow cụ thể.
- [x] Rà `waku/loop/agent.py` để chốt loop tách khỏi gateway.
- [x] Rà `waku/runtime/session.py` để ghi nhận working memory/session là lớp riêng.

## Phase 2: Audit Niko Hiện Tại

Mục đích: chốt hiện trạng thật của Niko sau Phase 6C, tránh lập plan dựa trên
kiến trúc cũ.

- [x] Xác nhận `bots/telegram/` hiện là gateway mỏng, nhưng vẫn gọi graph trực tiếp.
- [x] Xác nhận `niko/chat_gateway.py` là message abstraction hiện có.
- [x] Xác nhận `niko/loop/` đã có Loop core V0.
- [x] Xác nhận `niko/tools/` đã chứa memory/Jira tool adapters.
- [x] Xác nhận `niko/graphs/jira_issue/` đã là workflow nghiệp vụ riêng.
- [x] Ghi rõ `ChatReplyGraph` đang ôm route, correction, Jira slot, Deep job,
      trace/chat log và reply delivery callback.
      Ghi chú sau triển khai: Phase 4 đã chuyển Jira selection và memory
      correction call-site lên `NikoApp`.

## Phase 3: Chốt Target Boundary

Mục đích: mô tả đích tách lớp đủ rõ để vòng sau không phải tranh luận lại tên
lớp và trách nhiệm.

- [x] Đề xuất `bots/<gateway>/` chỉ giữ platform IO.
- [x] Đề xuất `niko/gateway/` cho gateway runner chung.
- [x] Đề xuất `NikoApp` hoặc app factory làm assembly root.
- [x] Đề xuất boundary chọn workflow cấp turn; sau review, không giữ package riêng
      nếu package đó chỉ delegate.
- [x] Giữ `niko/graphs/` cho workflow nghiệp vụ cụ thể.
- [x] Giữ `niko/loop/` cho tool-loop runtime generic.
- [x] Giữ `niko/tools/` cho tool adapters theo domain.

## Phase 4: Viết Migration Strategy

Mục đích: biến target architecture thành các phase refactor nhỏ, có thể triển
khai và test từng phần.

- [x] Phase 1 tương lai: tạo gateway runner khung nhưng giữ behavior cũ.
- [x] Phase 2 tương lai: tạo `NikoApp` assembly root.
- [x] Phase 3 tương lai: review boundary theo Waku trước khi tạo lớp mới.
- [x] Phase 4 tương lai: đưa Jira workflow selection ra khỏi `ChatReplyGraph`.
- [x] Phase 5 tương lai: đưa memory correction selection ra lớp chọn workflow cấp turn.
- [x] Phase 6 tương lai: cập nhật dashboard/docs và cleanup.

## Phase 5: Đồng Bộ Docs

Mục đích: các docs hiện tại biết có plan tách mới, nhưng không mô tả nhầm rằng
code đã được refactor.

- [x] Tạo `docs/plans/2026-10-08-niko-core-split-survey.md`.
- [x] Tạo `docs/plans/2026-10-08-niko-core-split-survey-checklist.md`.
- [x] Cập nhật `docs/plans/README.md` để liệt kê plan/checklist mới.
- [x] Cập nhật `docs/harness/architecture.md` với mục target refactor ngắn.
- [x] Cập nhật `docs/loop/architecture.md` để nói Loop không phải app/gateway
      orchestrator.
- [x] Cập nhật `AGENTS.md` để session sau biết đây là plan khảo sát, chưa phải
      code đã triển khai.

## Phase 6: Verification

Mục đích: đảm bảo thay đổi là docs-only và không để lại wording gây hiểu nhầm.

- [x] Chạy `rtk rg` cho các cụm `gateway runner`, `TurnOrchestrator`,
      `ChatReplyGraph`, `tool router`, `planned`.
- [x] Chạy `rtk git diff --check`.
- [x] Xác nhận `git status --short` chỉ có Markdown/AGENTS thay đổi.
