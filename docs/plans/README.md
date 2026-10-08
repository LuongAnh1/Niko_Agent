# Kế Hoạch Triển Khai

Folder này chứa các kế hoạch ngắn hạn hoặc kế hoạch liên quan trực tiếp đến triển khai.
Roadmap dài hạn không đặt ở đây; dùng `docs/memory/`, `docs/loop/` hoặc folder domain tương ứng.

## Quy Ước

- Tên file bắt đầu bằng ngày `YYYY-MM-DD`.
- Header trong file ghi ngày lập/cập nhật khi tài liệu là plan/checklist đang hoạt động.
- Nội dung phải chốt rõ phạm vi, giả định, các phase và test cần có.
- Chia theo thư mục con để người đọc biết tài liệu thuộc lane nào.
- Checklist live test cần có mục đích, bước kiểm thử, expected trace/log và chỗ ghi kết quả.

## Cấu Trúc

- `memory/`: kế hoạch và checklist cho chat memory, Decision Model memory gate, live memory test.
- `loop/`: kế hoạch và checklist cho Loop core, memory tools, dashboard observability, Jira tool lane.
- `jira/`: checklist live test riêng cho Jira runtime tools qua Telegram/dashboard.
- `core-split/`: khảo sát và triển khai tách gateway, app assembly, graph/workflow.
- `maintenance/`: checklist đồng bộ docs/source commentary và các đợt rà tài liệu.

## Memory

- [Kế hoạch Chat Memory Decision Model](memory/2026-10-07-chat-memory-decision-model.md)
- [Checklist Chat Memory Decision Model](memory/2026-10-07-chat-memory-decision-model-checklist.md)
- [Checklist Live Test Chat Memory Phase 6/7](memory/2026-10-07-chat-memory-live-test-checklist.md) - historical, kết quả chính đã được gom vào `../harness/memory-live-verification.md`.

## Loop Và Tools

- [Kế hoạch Niko Loop 2026-10-08](loop/2026-10-08-niko-loop-implementation-plan.md)
- [Checklist Niko Loop 2026-10-08](loop/2026-10-08-niko-loop-implementation-checklist.md)

## Jira

- [Checklist Live Test Jira Runtime Tools 2026-10-08](jira/2026-10-08-jira-live-test-checklist.md)

## Core Split

- [Kế hoạch khảo sát tách core/gateway/graph 2026-10-08](core-split/2026-10-08-niko-core-split-survey.md)
- [Checklist khảo sát tách core/gateway/graph 2026-10-08](core-split/2026-10-08-niko-core-split-survey-checklist.md)
- [Kế hoạch triển khai tách core/gateway/graph 2026-10-08](core-split/2026-10-08-niko-core-split-implementation-plan.md)
- [Checklist triển khai tách core/gateway/graph 2026-10-08](core-split/2026-10-08-niko-core-split-implementation-checklist.md)

## Maintenance

- [Checklist đồng bộ docs/source commentary 2026-10-08](maintenance/2026-10-08-docs-source-sync-checklist.md)
- [Kế hoạch Dashboard Decision Config 2026-10-08](maintenance/2026-10-08-dashboard-decision-config-plan.md)
- [Checklist Dashboard Decision Config 2026-10-08](maintenance/2026-10-08-dashboard-decision-config-checklist.md)
