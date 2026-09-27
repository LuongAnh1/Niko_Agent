# Tài Liệu Niko Agent

Thư mục này mô tả Niko Agent theo góc nhìn kỹ thuật và demo đồ án. Niko hiện là một harness local có Telegram gateway, Fast/Deep agent flow, SQLite memory baseline, JSONL trace và Mini Ops dashboard.

## Nên Đọc Theo Thứ Tự

1. [Kiến trúc](architecture.md): module chính, ranh giới trách nhiệm, runtime, memory, ops.
2. [Luồng chat Telegram](telegram-chat-flow.md): từ Telegram message đến local/fast/deep reply.
3. [Harness Memory & Ops](niko-harness-memory-ops.md): SQLite memory, trace JSONL, dashboard và API.
4. [Nghiệp vụ harness](business-domains/README.md): Telegram gateway hiện tại, Jira gateway dự kiến và hướng nâng cấp memory.
5. [Demo Guide](demo-guide.md): cách chạy bot/dashboard và các kịch bản demo.
6. [Memory Roadmap](memory-roadmap.md): baseline hiện tại và hướng nâng cấp Semantic/Episodic Memory.

## Tài Liệu Liên Quan

- [README root](../README.md): hướng dẫn chạy nhanh.
- [AGENTS.md](../AGENTS.md): ghi chú ngắn cho Codex khi mở phiên mới.
- [niko/HOOK.md](../niko/HOOK.md): persona/hook nạp vào prompt của Niko.

## Ranh Giới Tài Liệu

Các tài liệu này mô tả trạng thái hiện tại của repo `Niko_Agent`, không phải toàn bộ đồ án Lakehouse/Knowledge Graph. Lakehouse và Knowledge Graph sẽ được phát triển ở lớp cải tiến memory sau, còn repo này đóng vai trò harness baseline để chứng minh hệ thống chạy thật và có dữ liệu trace/memory ban đầu.
