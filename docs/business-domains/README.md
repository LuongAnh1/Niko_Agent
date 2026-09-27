# Nghiệp Vụ Của Niko Agent Harness

Folder này mô tả các nghiệp vụ/cổng dữ liệu mà `Niko_Agent` có thể đảm nhiệm
trong vai trò một agent harness.

Điểm cần chốt:

```text
Niko_Agent không chỉ là chatbot Telegram.
Niko_Agent là harness có thể nhận dữ liệu từ nhiều gateway.
Telegram là gateway hội thoại hiện tại.
Jira là gateway nghiệp vụ/task dự kiến.
Memory backend là lớp giúp agent đọc, liên kết và phân tích dữ liệu có ngữ cảnh.
```

## 1. Vì Sao Cần Tách Nghiệp Vụ Riêng

Nếu chỉ nhìn Niko như một bot trả lời chat, hệ thống dễ bị hiểu là:

```text
user nhắn -> LLM trả lời -> lưu chat
```

Nhưng mục tiêu dài hơn của repo là tạo một harness có thể:

- nhận input từ người dùng;
- nhận dữ liệu nghiệp vụ từ hệ thống khác;
- định tuyến tác vụ cho Fast/Deep agent;
- truy xuất memory;
- ghi trace;
- cung cấp dashboard quan sát;
- làm nền cho memory backend nâng cao.

Vì vậy cần một tài liệu nghiệp vụ riêng để giải thích:

- Niko đang phục vụ loại luồng nghiệp vụ nào;
- gateway nào là input hội thoại, gateway nào là input nghiệp vụ;
- dữ liệu nào cần đưa vào memory;
- vì sao memory hiện tại còn rời rạc;
- hướng nâng cấp thành hạ tầng memory/lakehouse/graph.

## 2. Các Nghiệp Vụ/Gateway Dự Kiến

| Thành phần | Trạng thái | Vai trò |
| --- | --- | --- |
| Telegram Gateway | đã có baseline | cổng hội thoại realtime với người dùng |
| Jira Gateway | dự kiến | cổng đọc task/issue/comment/log/component theo dự án |
| Memory Backend | baseline SQLite, sẽ nâng cấp | lưu và truy xuất Semantic/Episodic Memory |
| Ops Dashboard | đã có baseline | quan sát turn, trace, memory, route |
| Lakehouse/Graph Layer | hướng nâng cấp | chuẩn hóa, liên kết, khai phá dữ liệu phục vụ agent |

## 3. Luồng Tổng Quát

```text
Telegram
  -> user asks / gives instruction
  -> Niko routes local/fast/deep

Jira
  -> project/task/comment/log/component data
  -> ingestion/sync
  -> memory records

Memory Backend
  -> semantic facts
  -> episodic events
  -> related tasks/issues
  -> context for Deep agent

Deep Agent
  -> reads retrieved context
  -> analyzes task/problem
  -> replies with traceable reasoning
```

## 4. Tài Liệu Trong Folder Này

1. [Telegram Gateway Domain](telegram-gateway.md): nghiệp vụ hiện tại của cổng
   Telegram.
2. [Jira Gateway Domain](jira-gateway.md): nghiệp vụ dự kiến khi Niko đọc task,
   log, comment, component từ Jira.
3. [Memory Upgrade Path](memory-upgrade-path.md): vì sao đọc rời rạc chưa đủ và
   hướng nâng cấp thành memory backend có hạ tầng dữ liệu.

