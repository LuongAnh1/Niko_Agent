# Tài Liệu Niko Agent

Thư mục này mô tả Niko Agent theo góc nhìn kỹ thuật và demo đồ án. Niko hiện là một harness local có Telegram gateway, Fast/Deep agent flow, SQLite memory baseline, JSONL trace và Mini Ops dashboard.

## Nên Đọc Theo Thứ Tự

1. [Kiến trúc](harness/architecture.md): module chính, ranh giới trách nhiệm, runtime, memory, ops.
2. [Luồng chat Telegram](harness/telegram-chat-flow.md): từ Telegram message đến local/fast/deep reply.
3. [Harness Memory & Ops](harness/memory-ops.md): SQLite memory, trace/runtime log, dashboard, runtime config và API.
4. [Nghiệp vụ harness](business-domains/README.md): Telegram gateway hiện tại, Jira gateway dự kiến và hướng nâng cấp memory backend.
5. [Demo Guide](demo/demo-guide.md): cách chạy bot/dashboard và các kịch bản demo.
6. [Sơ đồ Chat Memory](memory/chat-memory-architecture-flow.md): kiến trúc và luồng xử lý memory hiện tại/đích đến.
7. [Niko Loop Architecture](loop/architecture.md): Loop core V0, memory fact tools và hướng mở sang Jira/business tools.
8. [Kế hoạch Chat Memory Decision Model 2026-10-07](plans/memory/2026-10-07-chat-memory-decision-model.md): kế hoạch ngắn hạn cho chat memory single-user và Decision Model.
9. [Kế hoạch Niko Loop 2026-10-08](plans/loop/2026-10-08-niko-loop-implementation-plan.md): phase triển khai Loop core, memory tools, observability và Jira lane.
10. [Checklist Live Test Jira Runtime Tools 2026-10-08](plans/jira/2026-10-08-jira-live-test-checklist.md): checklist live test Jira qua Telegram/dashboard.
11. [Memory Roadmap](memory/roadmap.md): baseline hiện tại, chat memory, và ranh giới với lakehouse/Jira memory backend.

## Bố Cục Folder

- `harness/`: kiến trúc runtime, luồng Telegram, dashboard, trace và SQLite baseline.
- `business-domains/`: gateway/nghiệp vụ như Telegram, Jira và memory backend.
- `memory/`: roadmap dài hạn cho memory trong repo Niko.
- `plans/`: kế hoạch ngắn hạn/liên quan triển khai; bên trong chia theo `memory/`, `loop/`, `jira/`, `core-split/`, `maintenance/`.
- `demo/`: kịch bản demo và hướng dẫn trình bày.

## Tài Liệu Liên Quan

- [README root](../README.md): hướng dẫn chạy nhanh.
- [AGENTS.md](../AGENTS.md): ghi chú ngắn cho Codex khi mở phiên mới.
- [niko/HOOK.md](../niko/HOOK.md): persona/hook nạp vào prompt của Niko.

## Ranh Giới Tài Liệu

Các tài liệu này mô tả trạng thái hiện tại của repo `Niko_Agent`. Lakehouse/Jira
memory backend là lane riêng trong repo `Ai-Memory-Lakehouse-Graph-Mining`; Niko
chỉ giữ baseline chat memory local và điểm nối retrieval/tool khi cần dữ liệu
nghiệp vụ.
