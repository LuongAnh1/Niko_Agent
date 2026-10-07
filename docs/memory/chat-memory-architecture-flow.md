# Chat Memory Architecture Flow

Ngày cập nhật: 2026-10-07
Phạm vi: chat memory local của Niko Agent, single-user v1

Tài liệu này là bản sơ đồ kiểm soát luồng memory. Nó gom lại trạng thái hiện tại
và kiến trúc muốn xây theo kế hoạch trong `docs/plans/2026-10-07-chat-memory-decision-model.md`.
Mục tiêu là nhìn vào đây để biết dữ liệu đi qua đâu, quyết định nào do model nhỏ
phụ trách, phần nào đã có, phần nào còn là phase sau.

## Legend

- `done`: đã có trong code hiện tại.
- `planned`: hướng muốn xây tiếp, chưa hoàn chỉnh.
- `boundary`: ranh giới không nên trộn vào chat memory v1.

## 1. Ranh Giới Lớn

```mermaid
flowchart LR
    Telegram[Telegram chat] --> Niko[Niko_Agent chat memory]
    Niko --> SQLite[(SQLite local<br/>chat_log / facts / episodes)]
    Niko --> Dashboard[Ops Dashboard<br/>trace / runtime log / memory CRUD]

    Jira[Jira / tài liệu / issue data] --> Lakehouse[Ai-Memory-Lakehouse-Graph-Mining]
    Lakehouse --> KG[Lakehouse / Knowledge Graph / graph mining]

    KG -. future retrieval/tool slot .-> Niko

    classDef done fill:#dff5e1,stroke:#2e7d32,color:#111;
    classDef planned fill:#fff4cc,stroke:#b7791f,color:#111;
    classDef boundary fill:#f3f4f6,stroke:#6b7280,color:#111;

    class Telegram,Niko,SQLite,Dashboard done;
    class KG planned;
    class Jira,Lakehouse boundary;
```

Chat memory của Niko là lane local cho tương tác Telegram. Lakehouse/Jira là lane
business memory riêng, chỉ nên nối vào khi cần dữ liệu issue/tài liệu, không dùng
làm nơi lưu mặc định cho mọi tin nhắn Telegram.

## 2. Kiến Trúc Memory Runtime

```mermaid
flowchart TB
    Gateway[Gateway<br/>Telegram] --> ChatGraph[ChatReplyGraph<br/>route / deep job / reply]
    ChatGraph --> DeepRuntime[niko.runtime<br/>build Deep prompt]
    DeepRuntime --> MemoryRuntime[MemoryRuntime<br/>memory pipeline]

    MemoryRuntime --> RetrievalGate{Retrieval gate<br/>skip / retrieve}
    RetrievalGate -->|skip| NoMemory[No memory context]
    RetrievalGate -->|retrieve + query| Retriever[Search/list memory]
    RetrievalGate -->|error fail-open| Retriever

    Retriever --> Store[(MemoryStore SQLite)]
    Store --> Formatter[format_memory_context]
    Formatter --> DeepPrompt[Deep prompt<br/>identity + memory + current message]

    ChatGraph --> WritePath[MemoryRuntime write path]
    WritePath --> ChatLog[(chat_log<br/>operational log)]
    WritePath --> WriteGate{Write gate<br/>discard / remember}
    WriteGate -->|discard| NoEpisode[No long-term episode]
    WriteGate -->|remember| Episode[(episodes)]
    WriteGate -->|error fail-open| Episode

    MemoryRuntime --> Trace[Trace JSONL]
    MemoryRuntime --> RuntimeLog[Runtime log<br/>Bots dashboard]

    classDef done fill:#dff5e1,stroke:#2e7d32,color:#111;
    classDef planned fill:#fff4cc,stroke:#b7791f,color:#111;
    classDef boundary fill:#f3f4f6,stroke:#6b7280,color:#111;

    class Gateway,ChatGraph,DeepRuntime,MemoryRuntime,RetrievalGate,Retriever,Store,Formatter,DeepPrompt,WritePath,ChatLog,WriteGate,Episode,Trace,RuntimeLog,NoMemory,NoEpisode done;
```

Điểm kiểm soát chính là `MemoryRuntime`. Graph và runtime không nên tự biết chi
tiết gate/search/write nữa; chúng chỉ gọi pipeline memory.

## 3. Retrieval Flow Cho Deep

```mermaid
sequenceDiagram
    participant User as User message
    participant Graph as ChatReplyGraph
    participant Deep as niko.runtime
    participant Mem as MemoryRuntime
    participant DM as Ollama/Nimble
    participant Store as SQLite facts/episodes
    participant Trace as Trace/Ops

    User->>Graph: accepted message
    Graph->>Deep: call_deep_agent(prompt)
    Deep->>Mem: retrieve_for_deep(prompt, gateway_message)

    alt retrieval disabled
        Mem-->>Deep: empty RetrievedMemory
    else inventory question
        Mem->>Store: list_facts / recent_episodes
        Mem->>Trace: memory_retrieval
        Mem-->>Deep: formatted memory context
    else retrieval gate enabled
        Mem->>DM: decide_memory_retrieval
        alt skip
            Mem->>Trace: memory_gate_decision skip
            Mem-->>Deep: no memory context
        else retrieve
            Mem->>Store: search_facts/search_episodes(query)
            Mem->>Trace: memory_retrieval + gate metadata
            Mem-->>Deep: formatted memory context
        else gate error
            Mem->>Trace: memory_gate_error
            Mem->>Store: search with raw prompt
            Mem-->>Deep: formatted memory context
        end
    else retrieval gate disabled
        Mem->>Store: search with raw prompt
        Mem->>Trace: memory_retrieval
        Mem-->>Deep: formatted memory context
    end

    Deep-->>Graph: deep answer
```

Nguyên tắc: retrieval gate lỗi thì fail-open, vì bỏ lỡ memory cần thiết thường tệ
hơn việc retrieve hơi dư. Fast triage không nhận memory context để giữ route JSON sạch.

## 4. Write Flow Hiện Tại

```mermaid
sequenceDiagram
    participant Graph as ChatReplyGraph
    participant Mem as MemoryRuntime
    participant DM as Ollama/Nimble
    participant Store as SQLite
    participant Trace as Trace/Ops

    Graph->>Mem: record_chat_log(...)
    Mem->>Store: insert chat_log
    Mem->>Trace: memory_write_chat_log

    Graph->>Mem: record_deep_episode(prompt, answer, followups)

    alt memory write disabled
        Mem-->>Graph: no-op
    else write gate disabled
        Mem->>Store: add_episode(summary)
        Mem->>Trace: memory_write_episode
    else write gate enabled
        Mem->>DM: decide_memory_write
        alt discard
            Mem->>Trace: memory_write_decision discard
            Mem-->>Graph: no episode
        else remember
            Mem->>Trace: memory_write_decision remember
            Mem->>Store: add_episode(summary)
            Mem->>Trace: memory_write_episode
        else gate error
            Mem->>Trace: memory_write_gate_error
            Mem->>Trace: memory_write_decision remember/fail-open
            Mem->>Store: add_episode(summary)
        end
    end
```

Write gate v1 chỉ chặn `episodes`. `chat_log` vẫn là operational log để debug,
dashboard và consolidation sau này có nguyên liệu.

## 5. Target Flow: Consolidation

```mermaid
flowchart TB
    ChatLog[(chat_log<br/>operational log)] --> Threshold{Đủ N exchange<br/>chưa consolidated?}
    Threshold -->|no| Wait[Chờ thêm hội thoại]
    Threshold -->|yes| Batch[Load batch cố định]
    Batch --> CandidateBuilder[Rule-based candidate builder]
    CandidateBuilder --> Classifier{Memory type classifier}
    Classifier -->|semantic_fact| Facts[(facts)]
    Classifier -->|episodic_event| Episodes[(episodes)]
    Classifier -->|discard| NoMemory[Không tạo long-term memory]
    Facts --> Mark[Mark đúng rows đã đọc<br/>consolidated=1]
    Episodes --> Mark
    NoMemory --> Mark
    Classifier -->|error / invalid output| Retry[Không mark rows<br/>retry lần sau]

    classDef done fill:#dff5e1,stroke:#2e7d32,color:#111;
    classDef planned fill:#fff4cc,stroke:#b7791f,color:#111;

    class ChatLog,Batch,CandidateBuilder,Classifier,Facts,Episodes,NoMemory,Mark,Retry done;
    class Threshold,Wait planned;
```

Scaffold consolidation hiện đã có: `chat_log` có `consolidated`, store đọc được batch
chưa xử lý, tạo candidate bảo thủ, dùng classifier `semantic_fact` / `episodic_event`
/ `discard`, ghi facts/episodes với provenance `consolidation`, và có API dashboard
để chạy thủ công. Phần chưa làm là threshold/scheduler tự động và summarizer tự do;
guardrail hiện tại vẫn là: classifier lỗi thì không mark row đã consolidate.

## 6. Target Flow: Correction / Forget Memory

```mermaid
flowchart TB
    Prompt[Incoming prompt] --> Intent{memory_correction_intent<br/>planned}
    Intent -->|none| NormalFlow[Luồng chat bình thường]
    Intent -->|correct_memory| Search[Search facts/episodes liên quan]
    Intent -->|forget_memory| Search
    Search --> Confidence{Match đủ chắc?}
    Confidence -->|yes, correct| Update[Update fact/episode có trace]
    Confidence -->|yes, forget| Delete[Delete fact/episode có trace]
    Confidence -->|no| Clarify[Hỏi lại user hoặc route Deep giải thích]

    Update --> Trace[memory_correction_decision / memory_write_* trace]
    Delete --> Trace
    Clarify --> NormalFlow

    classDef done fill:#dff5e1,stroke:#2e7d32,color:#111;
    classDef planned fill:#fff4cc,stroke:#b7791f,color:#111;

    class NormalFlow done;
    class Prompt,Intent,Search,Confidence,Update,Delete,Clarify,Trace planned;
```

Deep agent không nên tự sửa/xóa memory tùy ý. Correction cần search lấy record ID
trước, rồi mới update/delete có trace.

## 7. Các Lớp Dữ Liệu

```mermaid
flowchart LR
    Current[Current user message] --> Working[Working memory mỗi turn<br/>identity + retrieved memory + current message]
    Recent[Recent conversation window<br/>planned] --> Working
    Facts[(facts<br/>semantic memory)] --> Working
    Episodes[(episodes<br/>episodic memory)] --> Working

    ChatLog[(chat_log<br/>operational log)] --> Consolidation[Consolidation<br/>planned]
    Consolidation --> Facts
    Consolidation --> Episodes

    Manual[Dashboard/manual fact] --> Facts

    classDef done fill:#dff5e1,stroke:#2e7d32,color:#111;
    classDef planned fill:#fff4cc,stroke:#b7791f,color:#111;

    class Current,Working,Facts,Episodes,ChatLog,Manual done;
    class Recent,Consolidation planned;
```

`chat_log` không phải semantic/episodic memory. Nó là log vận hành và nguyên liệu
cho consolidation. Long-term memory hiện nằm ở `facts` và `episodes`.

## 8. Trạng Thái Theo Phase

| Phase | Mục tiêu | Trạng thái |
| --- | --- | --- |
| 0 | Ranh giới chat memory vs lakehouse/Jira | done |
| 0.5 | `MemoryRuntime` làm cổng pipeline trung tâm | done |
| 1 | Decision model memory tasks | retrieval/write done, classifier/correction planned |
| 2 | Retrieval gate cho Deep | done, default-off |
| 3 | Unicode/query search hardening | done |
| 4 | Write gate và consolidation | write gate done, manual consolidation candidate/classifier done, auto threshold planned |
| 5 | Correction/forget qua chat/dashboard | planned |
| 6 | Working memory rõ: recent/current/long-term | planned |
| 7 | Eval riêng cho memory | partial tests done, eval scenarios planned |

## 9. Nguyên Tắc Kiểm Soát

1. Telegram gateway không chứa logic memory.
2. `ChatReplyGraph` điều phối flow chat, nhưng không ôm policy retrieval/write.
3. `MemoryRuntime` là cổng chính cho memory pipeline.
4. Decision model chỉ trả label/query/reason, không tự ghi database.
5. SQLite là source of truth cho v1.
6. `chat_log` luôn là operational log; write gate chỉ chặn long-term `episodes`.
7. Retrieval gate lỗi thì fail-open để không bỏ lỡ memory cần dùng.
8. Write/consolidation/correction lỗi thì không được làm mất log vận hành.
9. Mọi quyết định memory phải có trace/runtime log đủ đọc.
10. Multi-user scoped memory và lakehouse/Jira retrieval không nằm trong v1 mặc định.
