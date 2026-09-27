# Hướng Nâng Cấp Memory Từ Dữ Liệu Nghiệp Vụ

## 1. Vấn Đề Hiện Tại

Baseline hiện tại của `Niko_Agent` đã có:

- Telegram gateway;
- local/fast/deep routing;
- SQLite `chat_log`, `facts`, `episodes`;
- JSONL trace;
- Mini Ops dashboard.

Nhưng memory hiện tại vẫn còn đơn giản:

- facts chủ yếu thêm thủ công;
- episodes chủ yếu sinh sau deep job;
- retrieval dựa trên text search;
- chưa có graph relation giữa task, issue, comment, component, user, episode;
- chưa có ingestion đều đặn từ hệ thống nghiệp vụ như Jira.

Vì vậy, agent vẫn có nguy cơ đọc dữ liệu rời rạc:

```text
người dùng paste một đoạn issue
-> agent phân tích trên đoạn đó
-> thiếu comment cũ
-> thiếu status history
-> thiếu issue liên quan
-> thiếu evidence
```

## 2. Mục Tiêu Nâng Cấp

Mục tiêu là biến memory từ một kho lưu chat/fact đơn giản thành một hạ tầng đọc
ngữ cảnh cho agent.

```text
Jira/Telegram/runtime data
  -> ingestion
  -> normalized memory records
  -> semantic + episodic memory
  -> graph relationships
  -> retrieval context
  -> Deep agent analysis
```

## 3. Semantic Và Episodic Trong Jira

| Loại memory | Nguồn Jira | Ý nghĩa |
| --- | --- | --- |
| Semantic Memory | summary, description, comment, resolution, component, label | tri thức/fact/ngữ cảnh nghiệp vụ |
| Episodic Memory | created, assigned, status changed, commented, linked, reopened, closed | sự kiện và diễn biến theo thời gian |

Ví dụ:

```text
Semantic:
  "sync-service thường gặp timeout khi batch size quá lớn"

Episodic:
  "PROJ-123 chuyển từ Resolved sang Reopened vào 2026-10-12 sau comment của QA"
```

## 4. Vì Sao Cần Hạ Tầng Lưu Trữ Và Cập Nhật Thường Xuyên

Nếu chỉ đọc Jira trực tiếp từng lần hỏi, agent sẽ gặp các vấn đề:

- chậm vì phải gọi API nhiều;
- khó giữ lịch sử trạng thái;
- khó so sánh giữa nhiều issue;
- khó đo chất lượng retrieval;
- khó làm graph mining;
- khó audit LLM đã đọc dữ liệu nào;
- khó tái chạy phân tích khi logic thay đổi.

Hạ tầng lưu trữ giúp:

- cache dữ liệu Jira đã đọc;
- chuẩn hóa schema;
- cập nhật định kỳ;
- lưu raw và cleaned data;
- tạo node/edge graph;
- cung cấp context có nguồn;
- phục vụ dashboard và đánh giá.

## 5. Các Giai Đoạn Đề Xuất

### Phase 1: Harness Baseline

Trạng thái hiện tại:

```text
Telegram -> ChatReplyGraph -> Deep Agent
                  |
                  v
         SQLite memory + JSONL trace
```

Mục tiêu:

- chứng minh agent chạy thật;
- có trace;
- có baseline memory;
- có dashboard quan sát.

### Phase 2: Jira Gateway PoC

Thêm một cổng đọc Jira ở mức nhỏ:

```text
Jira issue key / project filter
  -> fetch issue/comment/changelog/component
  -> normalize
  -> store as memory records
```

Mục tiêu:

- agent có dữ liệu task thật để phân tích;
- không cần copy/paste toàn bộ issue vào Telegram;
- bắt đầu có mapping giữa issue và memory.

### Phase 3: Memory Ingestion Pipeline

Chuẩn hóa dữ liệu thành bảng/bản ghi riêng:

```text
issues
comments
change_events
actors
components
issue_links
semantic_records
episodic_records
```

Mục tiêu:

- tách rõ raw data và memory records;
- update định kỳ;
- kiểm tra missing/duplicate;
- tạo input ổn định cho retrieval.

### Phase 4: Lakehouse/Graph Backend

Đưa dữ liệu sang lớp nâng cấp:

```text
Bronze: raw Jira/API/export data
Silver: cleaned issue/comment/event tables
Gold: graph-ready tables + memory records
Neo4j: issue/task/component/comment/event graph
```

Mục tiêu:

- agent truy xuất theo quan hệ;
- hỗ trợ GraphRAG;
- chạy graph mining;
- đo bottleneck/pattern/reopen/community.

### Phase 5: Agent-Aware Retrieval

Thay vì chỉ search text, retrieval có thể dùng:

- keyword search;
- vector search;
- graph traversal;
- rerank theo task/user/time/project;
- context budget;
- citation/evidence.

Mục tiêu:

```text
Deep agent không chỉ nhận text gần giống,
mà nhận context có cấu trúc, có nguồn, có quan hệ.
```

## 6. Liên Hệ Với Đồ Án Lakehouse/Graph

`Niko_Agent` là harness chạy thật.

Repo lakehouse/graph là hướng nâng cấp memory:

```text
Niko baseline memory
  -> chỉ ra dữ liệu còn rời rạc
  -> cần ingestion từ Jira
  -> cần chuẩn hóa Semantic/Episodic Memory
  -> cần Knowledge Graph để liên kết
  -> cần Graph Mining để phát hiện pattern
```

Vì vậy, Jira Gateway là cầu nối rất hợp lý giữa hai phần:

```text
Niko_Agent
  -> có nhu cầu đọc task thật
  -> sinh yêu cầu về memory backend

Ai-Memory-Lakehouse-Graph-Mining
  -> cung cấp hạ tầng dữ liệu/memory/graph
  -> trả context tốt hơn cho agent
```

## 7. Câu Chốt Cho Báo Cáo/Demo

Có thể diễn đạt:

> Ban đầu, Niko Agent có thể tương tác qua Telegram và lưu memory cơ bản. Tuy
> nhiên, khi agent cần phân tích task trong hệ thống nghiệp vụ như Jira, việc đọc
> từng đoạn dữ liệu rời rạc không đủ để hiểu lịch sử, quan hệ và nguyên nhân của
> vấn đề. Vì vậy cần mở rộng harness bằng một Jira Gateway và một memory backend
> có khả năng chuẩn hóa, cập nhật, liên kết dữ liệu thành Semantic/Episodic
> Memory, từ đó hỗ trợ LLM phân tích dựa trên context có nguồn và có cấu trúc.

