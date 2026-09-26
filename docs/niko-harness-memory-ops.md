# Niko Harness Memory & Ops

Tài liệu này mô tả baseline harness của Niko: trace JSONL, SQLite memory và Mini Niko Ops dashboard. Mục tiêu là có một hệ thống chạy thật, quan sát được, có dữ liệu runtime để sau này cải tiến memory.

## Thành Phần

- `niko/harness/trace.py`: ghi trace JSONL theo turn và event.
- `niko/memory/store.py`: SQLite memory store.
- `niko/memory/context.py`: retrieve semantic/episodic memory và build context cho Deep agent.
- `niko/ops/dashboard.py`: Mini Niko Ops dashboard chạy local bằng stdlib Python.

## Runtime State

Mặc định state nằm trong:

```text
niko/.runtime/
  niko_memory.sqlite3
  traces/YYYY-MM-DD.jsonl
  claude_sandbox/
```

`niko/.runtime/` không nên commit. Đây là dữ liệu local để demo, debug và làm nguồn phân tích baseline.

## Memory Model

SQLite hiện có ba nhóm dữ liệu:

| Bảng | Loại memory | Ý nghĩa |
| --- | --- | --- |
| `chat_log` | operational log | Lưu user/assistant message theo conversation |
| `facts` | Semantic Memory | Fact, rule, preference, thông tin ổn định |
| `episodes` | Episodic Memory | Sự kiện/tác vụ theo thời gian, nhất là deep job hoàn tất |

`chat_log` không phải Semantic/Episodic Memory theo nghĩa dùng để suy luận. Nó là log vận hành để xem lại hội thoại. Semantic/Episodic hiện nằm ở `facts` và `episodes`.

Nếu SQLite hỗ trợ FTS5, store dùng full-text search. Nếu không có FTS5, store fallback về LIKE search có giới hạn.

## Retrieval

Deep agent nhận memory context khi:

```env
NIKO_MEMORY_ENABLED=1
NIKO_MEMORY_RETRIEVAL_ENABLED=1
```

Flow retrieval:

```text
user prompt
  -> retrieve_memory_context
  -> search/list facts
  -> search/list episodes
  -> format memory context
  -> inject vào prompt Deep agent
```

Fast triage không nhận memory context để giữ JSON sạch.

## Memory Write

Khi một turn được xử lý:

- `chat_log` có thể được ghi cho incoming user message và assistant reply.
- Nếu Deep job hoàn tất, graph tạo một episode cơ bản gồm prompt, answer và followups.
- Semantic facts hiện chủ yếu được thêm thủ công qua dashboard hoặc API.

Các event trace liên quan:

- `memory_retrieval`
- `memory_write_chat_log`
- `memory_write_episode`
- `memory_write_error`

## Mini Niko Ops Dashboard

Chạy:

```bash
python -m niko.ops.dashboard
```

Mặc định:

- Host: `127.0.0.1`
- Port: `7777`
- URL: `http://127.0.0.1:7777`

Dashboard có các tab:

- Overview: live harness graph, preview facts/episodes, trace tail.
- Memory: thêm/xóa semantic facts, xem semantic facts và episodic events.
- Chat: xem recent chat log.
- Traces: xem JSONL trace event.
- Ops: xem endpoint và ranh giới baseline.

Dashboard chỉ nên chạy local trong v1. Nếu expose ra ngoài máy cá nhân thì cần thêm auth/reverse proxy.

## Dashboard Graph

Live graph không phải là graph mining. Đây là đồ thị quan sát harness runtime.

Ý nghĩa tuyến chính:

- `Gateway -> Router -> Reply`: local/busy reply.
- `Gateway -> Router -> Fast Agent -> Reply`: Fast trả lời ngay.
- `Gateway -> Router -> Memory Gate -> Loop/Deep Agent -> Reply`: Deep path.
- `Memory Gate -> Memory Records`: có retrieval từ facts/episodes.
- `Reply/turn events -> Trace/Ops`: trace/dashboard quan sát.

Turn vừa kết thúc được giữ sáng thêm một khoảng ngắn để dễ quan sát đường đi.

## API

- `GET /api/snapshot`
- `GET /api/traces`
- `GET /api/memory`
- `POST /api/memory/facts`
- `DELETE /api/memory/facts/{id}`

Ví dụ thêm fact:

```bash
curl -X POST http://127.0.0.1:7777/api/memory/facts ^
  -H "Content-Type: application/json" ^
  -d "{\"subject\":\"Project\",\"content\":\"Niko is a local agent harness.\"}"
```

## Env

```env
NIKO_STATE_DIR=niko/.runtime
NIKO_TRACE_ENABLED=1
NIKO_MEMORY_ENABLED=1
NIKO_MEMORY_RETRIEVAL_ENABLED=1
NIKO_MEMORY_WRITE_ENABLED=1
NIKO_MEMORY_TOP_K=4
NIKO_OPS_HOST=127.0.0.1
NIKO_OPS_PORT=7777
```

## Baseline Có Ý Nghĩa Gì

Baseline hiện tại giúp chứng minh:

- Agent có thể chạy qua một harness quan sát được.
- Memory context có thể được retrieve và inject vào Deep agent.
- Có dữ liệu chat/trace/episode để phân tích sau.
- Dashboard có thể dùng để demo luồng hoạt động.

Baseline cũng cố tình bộc lộ điểm yếu:

- Search còn text-based.
- Facts chưa được tự trích xuất ổn định.
- Episodes còn là summary đơn giản.
- Chưa có schema graph/Knowledge Graph.
- Chưa có lakehouse storage layer.

Các điểm yếu này chính là lý do để phát triển memory pipeline, lakehouse và graph mining ở bước sau.

## Ghi Chú Về Waku

Niko kế thừa ý tưởng từ Waku: memory store local, semantic/episodic separation, JSONL traces và dashboard quan sát. Niko không dùng provider abstraction, pricing, eval arena hay API loop của Waku trong v1, vì runtime chính vẫn là `fcc-claude` trên máy local.
