# Memory Roadmap

Tài liệu này nối trạng thái hiện tại của `Niko_Agent` với hướng phát triển đồ
án. Cần tách hai lane:

- Chat memory trong Niko: memory local cho tương tác Telegram/người dùng.
- Lakehouse/Jira memory backend: tầng dữ liệu nghiệp vụ riêng trong repo
  `Ai-Memory-Lakehouse-Graph-Mining`, dùng Public Jira/tài liệu/issue/comment
  làm nguồn dữ liệu.

Nếu anh muốn xem kế hoạch gần hơn cho memory trong chat/tương tác người dùng,
đọc thêm [Kế hoạch Chat Memory Decision Model 2026-10-07](../plans/2026-10-07-chat-memory-decision-model.md).
Tài liệu đó tập trung vào cách dùng local Ollama/Nimble như lớp Decision Model cho
retrieval gate, write gate, consolidation, correction intent, working memory và
các bước cải tiến chat memory single-user trong Niko.

## Vị Trí Của Repo Này

`Niko_Agent` không phải layer lakehouse cuối cùng. Repo này là harness baseline:

- Có agent chạy thật.
- Có Telegram input/output.
- Có route local/fast/deep.
- Có trace JSONL.
- Có SQLite memory.
- Có dashboard quan sát.

Baseline này tạo dữ liệu và vấn đề thực tế để chứng minh vì sao chat memory cần
được cải tiến. Lakehouse/Jira backend không nằm trong runtime chat memory v1; nó
là nguồn retrieval nghiệp vụ riêng khi Niko cần phân tích issue/tài liệu.

## Baseline Memory Hiện Tại

```text
SQLite
  chat_log   -> hội thoại đã xử lý
  facts      -> semantic facts thủ công
  episodes   -> episodic records sau deep job

Trace JSONL
  turn_start
  route_decision
  memory_retrieval
  deep_agent events
  memory_write events
  turn_end
```

Deep agent có thể nhận memory context từ `facts` và `episodes`. Fast triage không nhận memory để giữ JSON sạch.

## Điểm Mạnh Của Baseline

- Dễ chạy local.
- Dễ inspect dữ liệu.
- Dễ demo cho thầy thấy harness thật.
- Không phụ thuộc API provider trong code.
- Có dữ liệu runtime để phân tích: chat, episode, trace.

## Điểm Yếu Cần Cải Tiến

### Semantic Memory

Hiện tại:

- Facts chủ yếu thêm thủ công qua dashboard.
- Search dựa trên FTS/LIKE.
- Chưa có trích xuất fact tự động đáng tin cậy.
- Chưa có conflict resolution.
- Chưa có quan hệ giữa facts.

Hướng cải tiến:

- Fact extraction pipeline.
- Fact normalization.
- Entity/relation extraction.
- Embedding + rerank.
- Knowledge Graph để nối fact, entity, task, issue, user, topic.

### Episodic Memory

Hiện tại:

- Episodes chủ yếu sinh sau deep job.
- Summary còn đơn giản: prompt, answer, followups.
- Chưa có event taxonomy.
- Chưa phân biệt task state, decision, outcome, blocker.

Hướng cải tiến:

- Chuẩn hóa episode schema.
- Tách event theo type: request, decision, action, result, error, followup.
- Link episode với semantic facts và entities.
- Dùng graph mining để phát hiện pattern lặp lại.

### Retrieval

Hiện tại:

- Retrieval phụ thuộc text search.
- Query ngắn hoặc mơ hồ có thể lấy sai/thiếu.
- Không có rerank theo ngữ cảnh.
- Không có graph traversal.

Hướng cải tiến:

- Hybrid retrieval: keyword + vector + graph.
- Rerank theo task/user/conversation/time.
- Decision Model làm memory gate để quyết định có cần retrieve hay không.
- Context builder có budget và citation rõ ràng.

## Ranh Giới Với Lakehouse/Jira

Lakehouse/Jira memory backend không phải nơi lưu mặc định cho mọi tin Telegram.
Nó là tầng dữ liệu nghiệp vụ riêng:

```text
Jira issue/comment/changelog/tài liệu
  -> Bronze/Silver/Gold
  -> Semantic/Episodic records nghiệp vụ
  -> Knowledge Graph / Graph Mining
  -> context có nguồn cho Niko khi user hỏi về task/issue/project
```

Chat memory của Niko v1 vẫn ở SQLite local:

```text
Telegram chat
  -> chat_log / facts / episodes
  -> retrieval gate / write gate / consolidation
  -> memory context cho Deep agent
```

Sau này hai lane có thể nối với nhau qua retrieval/tool slot, nhưng không nên
trộn schema ngay từ baseline.

## Từ Baseline Sang Lakehouse

Luồng mở rộng dự kiến nếu xuất dữ liệu Niko sang phân tích dài hạn:

```text
Niko runtime data
  SQLite memory
  JSONL traces
  Telegram/chat logs
        |
        v
Bronze
  raw SQLite export
  raw JSONL traces
        |
        v
Silver
  cleaned turns
  normalized chat rows
  normalized facts
  normalized episodes
  extracted entities/events
        |
        v
Gold
  graph-ready tables
  semantic memory records
  episodic memory records
  retrieval/evaluation metrics
        |
        v
Knowledge Graph / Analytics / GraphRAG
```

Trong repo `Niko_Agent`, v1 chỉ dừng ở baseline SQLite/JSONL. Lakehouse và graph layer nên là bước cải tiến riêng để tránh làm harness quá nặng ngay từ đầu.

## Graph Schema Dự Kiến

Các node có thể có:

- `User`
- `Conversation`
- `Turn`
- `Message`
- `Fact`
- `Episode`
- `Task`
- `Topic`
- `Entity`
- `ToolCall`
- `Error`

Các edge có thể có:

- `USER_SENT_MESSAGE`
- `MESSAGE_IN_TURN`
- `TURN_ROUTED_TO`
- `TURN_RETRIEVED_FACT`
- `TURN_CREATED_EPISODE`
- `EPISODE_MENTIONS_ENTITY`
- `FACT_ABOUT_ENTITY`
- `TASK_HAS_OUTCOME`
- `ERROR_OCCURRED_IN_TURN`

Graph schema này chưa cần implement ngay trong Niko v1. Nó là đích cho giai đoạn cải tiến memory.

## Tiêu Chí Chứng Minh Cải Tiến

Baseline hiện tại nên được đo bằng các câu hỏi:

- Agent có retrieve đúng fact khi hỏi trực tiếp không?
- Agent có retrieve đúng fact khi hỏi gián tiếp không?
- Agent có nhớ task/followup theo thời gian không?
- Agent có phân biệt chat log với semantic/episodic memory không?
- Agent có giải thích được memory nào đã được dùng không?

Sau khi cải tiến:

- Retrieval đúng hơn với query mơ hồ.
- Ít hallucinate memory hơn.
- Facts/episodes có quan hệ rõ hơn.
- Dashboard/analytics chỉ ra được memory path.
- Có thể dùng GraphRAG hoặc graph mining để phân tích pattern.

## Kết Luận

`Niko_Agent` là nền chạy thật. Nó không cố giải quyết toàn bộ memory ngay trong v1. Giá trị của repo là tạo harness baseline đủ rõ để:

- demo agent hoạt động,
- ghi lại dữ liệu thật,
- chỉ ra memory hiện tại còn thô,
- và mở đường cho pipeline/lakehouse/knowledge graph ở giai đoạn sau.
