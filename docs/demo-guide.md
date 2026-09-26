# Demo Guide

Tài liệu này dùng để chạy demo Niko Agent ở mức harness baseline: Telegram bot, two-agent routing, memory retrieval, trace và Mini Niko Ops dashboard.

## Chuẩn Bị

Kiểm tra env:

- Root `.env` có `CLAUDE_CLI_COMMAND`, `CLAUDE_DEEP_AGENT_COMMAND`, `CLAUDE_WORKDIR`.
- `niko/.env` có `NIKO_AGENT_MODE=two_agent`, memory/trace bật.
- `bots/telegram/.env` có `TELEGRAM_BOT_TOKEN`.

Chạy test trước demo:

```bash
python -m unittest discover
```

## Chạy Bot Và Dashboard

Terminal 1:

```bash
python -m bots.telegram.bot
```

Terminal 2:

```bash
python -m niko.ops.dashboard
```

Mở dashboard:

```text
http://127.0.0.1:7777
```

## Kịch Bản 1: Local Reply

Gửi Telegram:

```text
@Niko2_Bot em ơi
```

Kỳ vọng:

- Bot trả lời nhanh.
- Dashboard sáng tuyến `Gateway -> Router -> Reply`.
- Không sáng `Memory Gate`, `Memory Records`, `Loop`.
- Trace có `route_decision` route `local_reply`.

Ý nghĩa demo: gateway và router hoạt động, nhưng chưa cần LLM/memory.

## Kịch Bản 2: Fast Reply

Gửi một câu nhẹ không cần phân tích sâu, ví dụ:

```text
@Niko2_Bot em tóm tắt đoạn chat được không?
```

Tùy cấu hình Fast Agent, có thể xảy ra:

- Fast trả lời ngay: `Gateway -> Router -> Fast Agent -> Reply`.
- Fast thấy cần Deep: đi tiếp qua `Memory Gate -> Loop`.

Ý nghĩa demo: Fast Agent là lớp triage/compose, không phải memory engine.

## Kịch Bản 3: Thêm Semantic Fact

Trong dashboard tab Memory, thêm fact:

```text
Subject: Thời tiết
Content: Thời tiết lúc nào cũng đẹp.
```

Sau đó gửi:

```text
@Niko2_Bot em có fact nào về thời tiết không?
```

Kỳ vọng:

- Route thường đi Deep vì có keyword `fact`.
- Dashboard sáng `Memory Gate`, `Memory Records`, `Loop`.
- Bot trả lời có dùng fact vừa thêm.
- Trace có `memory_retrieval`.

Ý nghĩa demo: Deep agent nhận memory context từ SQLite.

## Kịch Bản 4: Deep Job

Gửi:

```text
@Niko2_Bot phân tích giúp anh vì sao memory hiện tại còn yếu
```

Kỳ vọng:

- Bot gửi wait reply trước.
- Deep agent chạy nền.
- Dashboard giữ sáng vùng `Loop` khi Deep đang chạy.
- Khi xong, bot gửi final reply.
- `episodes` tăng thêm 1 record.

Ý nghĩa demo: harness có xử lý tác vụ lâu, trace và episodic memory ghi lại kết quả.

## Kịch Bản 5: Busy Followup

Trong lúc Deep đang chạy, gửi tiếp:

```text
@Niko2_Bot bổ sung thêm ý về semantic nhé
```

Kỳ vọng:

- Bot trả `busy_reply`.
- Followup được gắn vào deep job.
- Episode cuối có phần followups.

Ý nghĩa demo: harness có notion cơ bản về conversation/job đang chạy.

## Xem Dữ Liệu Sau Demo

Dashboard:

- Overview: xem tuyến chạy gần nhất.
- Memory: xem facts và episodes.
- Chat: xem chat log.
- Traces: xem event JSON.

File runtime:

```text
niko/.runtime/niko_memory.sqlite3
niko/.runtime/traces/YYYY-MM-DD.jsonl
```

## Troubleshooting

Nếu bot phản hồi chậm:

- Kiểm tra mạng tới Telegram.
- Kiểm tra `CLAUDE_TIMEOUT_SECONDS`.
- Kiểm tra `fcc-claude` có đang chạy được không.

Nếu sticker timeout:

```env
TELEGRAM_STICKERS_ENABLED=0
```

hoặc giảm:

```env
TELEGRAM_STICKER_TIMEOUT_SECONDS=3
```

Nếu dashboard không đổi sau khi sửa code:

1. Dừng dashboard bằng `Ctrl+C`.
2. Chạy lại `python -m niko.ops.dashboard`.
3. Reload browser.

Nếu muốn reset dữ liệu demo:

1. Dừng bot và dashboard.
2. Backup hoặc xóa `niko/.runtime/niko_memory.sqlite3`.
3. Backup hoặc xóa `niko/.runtime/traces/`.
4. Chạy lại bot/dashboard.
