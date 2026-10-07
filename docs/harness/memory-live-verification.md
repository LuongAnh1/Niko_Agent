# Memory Live Verification

Ngày lập: 2026-10-07
Phạm vi: kiểm tra live chat memory trên Telegram qua Ops dashboard

Tài liệu này dùng sau khi unit test đã pass. Mục tiêu là kiểm tra các gate memory
trong môi trường chạy thật: dashboard, Ollama/Nimble, Telegram Bot, trace và
runtime log.

## Chuẩn Bị

1. Chạy dashboard:

```bash
rtk python -m niko.ops.dashboard
```

2. Mở `http://127.0.0.1:7777`.
3. Trong tab `Bots`, bấm `Warmup` cho Decision Model.
4. Trong tab `Config`, bật từng key theo từng ca kiểm tra:
   - `NIKO_MEMORY_RETRIEVAL_ENABLED=1`
   - `NIKO_MEMORY_GATE_ENABLED=1`
   - `NIKO_MEMORY_WRITE_ENABLED=1`
   - `NIKO_MEMORY_WRITE_GATE_ENABLED=1`
5. Trong tab `Bots`, start Telegram Bot từ dashboard.

Nếu test đang tập trung vào retrieval gate, có thể giữ write gate tắt để log dễ
đọc hơn. Nếu test write gate, nên giữ retrieval gate bật để xem hai gate cùng
hiện trong log.

## Prompt Mẫu

| Mục tiêu | Prompt Telegram | Kỳ vọng chính |
| --- | --- | --- |
| Retrieval `skip` | `Em giải thích nhanh decorator trong Python là gì?` | `memory_gate_decision` có `decision=skip`; Deep prompt không cần memory context. |
| Retrieval `retrieve` | `Anh đã bảo em nhớ sở thích làm docs của anh là gì nhỉ?` | `memory_gate_decision` có `decision=retrieve`; trace `memory_retrieval` có `gate_query`. |
| Inventory bypass | `Hiện tại em đang lưu những fact nào về anh?` | Gate được bypass bằng `inventory_bypass`; facts được list thay vì search bằng chữ `fact`. |
| Write `discard` | `oke cảm ơn em` | `memory_write_decision` có `decision=discard`; không tạo episode mới. |
| Write `remember` | `Lên kế hoạch sửa memory runtime để tuần sau anh demo với thầy.` | `memory_write_decision` có `decision=remember`; có `memory_write_episode`. |
| Consolidation fact | `Ghi nhớ rằng anh thích checklist có mục đích rõ ràng.` | Memory tab preview có candidate `semantic_fact`; run once ghi fact source `consolidation`. |
| Consolidation discard | `haha oke` | Candidate `discard`; run once mark rows nhưng không ghi fact/episode. |

## Quan Sát Trên Dashboard

Trong tab `Bots` hoặc bảng runtime log, tìm các event:

- `memory_gate_decision`: có `decision`, `query`, `reason`, `confidence`, `model`.
- `memory_gate_error`: Ollama/Nimble lỗi; retrieval phải fail-open bằng raw prompt.
- `memory_retrieval`: có facts/episodes được retrieve và metadata gate.
- `memory_write_decision`: có `remember` hoặc `discard`.
- `memory_write_gate_error`: write gate lỗi; episode baseline vẫn được ghi nếu memory write bật.
- `memory_write_episode`: có episode mới khi write gate cho phép.
- `consolidation`: xem kết quả trong Memory tab sau khi bấm `Run once`.

Trong tab `Traces`, kiểm tra một turn Deep có đủ thứ tự tối thiểu:

```text
turn_start
route_decision
memory_retrieval
deep_agent_call_started
deep_agent_call_finished
memory_write_decision
memory_write_episode hoặc không có episode nếu discard
reply_delivered
turn_end
```

## Fallback Cần Đúng

- Nếu Ollama/Nimble tắt trong lúc retrieval gate bật, Deep vẫn chạy với retrieval
  fail-open và runtime log có `memory_gate_error`.
- Nếu write gate lỗi, `chat_log` vẫn phải được ghi. Lỗi gate không được làm mất
  operational log.
- Nếu consolidation classifier lỗi, batch không được mark `consolidated=1`; anh
  có thể chạy lại sau khi model ổn.
- Nếu config đến từ OS env, dashboard phải hiển thị field bị khóa và không ghi
  đè vào `config.json`.

## Khi Nào Coi Là Pass

- Dashboard Config hiển thị mô tả dễ hiểu cho các key memory/decision.
- Mỗi prompt mẫu tạo đúng event kỳ vọng trong Runtime Log hoặc Trace.
- Không có Telegram bot crash khi bật retrieval gate/write gate.
- Memory tab preview/run consolidation không ghi bừa khi prompt là small talk.
- Full test suite vẫn pass sau khi chỉnh config/docs.
