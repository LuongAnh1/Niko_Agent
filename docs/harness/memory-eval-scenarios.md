# Chat Memory Eval Scenarios

Ngày cập nhật: 2026-10-07
Phạm vi: eval nhỏ cho chat memory local của Niko Agent.

Tài liệu này gom các prompt mẫu để kiểm tra memory bằng cách có thể lặp lại,
không chỉ dựa vào cảm giác khi live test Telegram. Unit test tương ứng nằm ở
`tests/test_memory_eval_scenarios.py`.

## Mục Tiêu

- Chứng minh retrieval gate không kéo long-term memory khi không cần.
- Chứng minh câu hỏi cần memory lấy đúng fact.
- Chứng minh lỗi Ollama/Nimble không làm Deep mất fallback retrieval.
- Chứng minh write gate có thể bỏ qua lượt inspect/list memory.
- Chứng minh correction/forget mutate đúng fact và có trace.
- Chứng minh query tiếng Việt bỏ dấu vẫn tìm được fact có dấu.

## Scenario Bắt Buộc

| ID | Nhóm | Prompt mẫu | Setup | Kỳ vọng |
| --- | --- | --- | --- | --- |
| M-01 | No memory | `haha oke` | Có fact sẵn, retrieval gate trả `skip` | Không inject `Relevant semantic facts`, trace có `gate_decision=skip` |
| M-02 | Direct fact | `Anh thích checklist kiểu gì?` | Fact: `Anh thích checklist có mục đích rõ và chia theo phase` | Inject đúng fact vào `Relevant semantic facts` |
| M-03 | Indirect fact | `Lúc viết docs em nên trình bày thế nào?` | Retrieval gate trả query `checklist mục đích phase` | Inject đúng fact dù prompt không hỏi trực tiếp "anh thích gì" |
| M-04 | Gate failure | `checklist phases` | Retrieval decider lỗi như Ollama down | Fail-open bằng raw prompt, trace có `gate_error` |
| M-05 | Write discard | `hiện tại em nhớ fact nào?` | Write gate trả `discard` | Không tạo episode mới, trace có `memory_write_decision=discard` |
| M-06 | Correction forget | `Niko, quên fact anh thích checklist màu xanh` | Có một fact khớp duy nhất | Xóa đúng fact, trace có `memory_correction_applied` |
| M-07 | Vietnamese search | `so thich checklist muc dich` | Fact có dấu tiếng Việt | Search trả đúng fact |

## Live Telegram Smoke Test

Sau khi unit tests pass, chỉ cần live test tối thiểu:

1. Bật dashboard, warmup Decision Model và start Telegram Bot.
2. Hỏi một câu inventory: `hiện tại em có những fact gì về anh?`
3. Gửi một lệnh quên fact mơ hồ, xác nhận Niko hỏi lại thay vì xóa.
4. Trả lời bằng `fact #...`, xác nhận trace có `memory_correction_applied`.
5. Gửi một câu follow-up cần ngữ cảnh gần đây, kiểm tra trace `memory_retrieval`
   có `recent_turn_count`.

Live test chỉ xác nhận integration. Kết luận regression nên dựa vào unit tests
và scenario table phía trên.
