# Checklist Đồng Bộ Docs Và Source Commentary

Ngày lập: 2026-10-08
Phạm vi: quét Markdown và chú thích/docstring đầu file để khớp với trạng thái hiện tại của Niko Agent.

## Mục Đích

Giữ tài liệu, checklist và chú thích mã nguồn không bị lệch so với code đang chạy. Đợt rà này tập trung vào các thay đổi đã triển khai gần đây: Loop core V0, memory fact tools, correction loop default-off, durable pending trong SQLite, auto consolidation default-off, Jira issue context flow V0 và Jira Decision Gate default-off.

## 1. Markdown Canonical

Mục đích: chốt tài liệu nào là nguồn đọc chính, tài liệu nào chỉ còn giá trị lịch sử.

- [x] `README.md` mô tả đúng dashboard-first config, Ollama/Nimble, Loop core V0, durable pending, Jira issue context flow và Jira Decision Gate default-off.
- [x] `AGENTS.md` mô tả đúng boundary hiện tại cho `niko/loop/`, `niko/tools/`, `niko/memory/correction_workflow.py`, `niko/memory/correction_loop.py`, `niko/graphs/jira_issue/` và `bots/decision_model/jira.py`.
- [x] `docs/harness/memory-live-verification.md` là nhật ký live verification canonical.
- [x] `docs/plans/2026-10-07-chat-memory-live-test-checklist.md` được giữ như historical, không còn là checklist hiện tại.
- [x] `docs/plans/README.md` liệt kê đủ plan/checklist mới của Loop và checklist đồng bộ docs.

## 2. Memory Và Loop Docs

Mục đích: tránh hiểu nhầm Loop vẫn chỉ là planned hoặc pending correction còn phụ thuộc RAM.

- [x] `docs/loop/architecture.md` ghi rõ Loop core/ToolRegistry/MemoryTools đã có V0, Jira fixture tools, Jira issue context flow và Jira Decision Gate đã có V0, còn Jira bot/gateway thật là planned.
- [x] `docs/memory/chat-memory-architecture-flow.md` ghi rõ consolidation manual + auto default-off đã có.
- [x] `docs/harness/memory-eval-scenarios.md` bổ sung durable pending và pending guardrail vào bảng scenario.
- [x] Các docs vẫn tách rõ chat memory local với Jira/lakehouse business memory.

## 3. Source Commentary

Mục đích: chú thích đầu file phản ánh đúng vai trò code, không mô tả nhầm trạng thái cũ.

- [x] `niko/memory/correction_loop.py` nói rõ đây là bridge default-off cho direct correction prompt, không sở hữu pending bền.
- [x] `niko/tools/memory/facts.py` nói rõ fact tools là adapter Loop; file cũ `niko/memory/loop_tools.py` đã xóa.
- [x] `niko/graphs/jira_issue/workflow.py` nói rõ đây là workflow nghiệp vụ bọc Jira tools và đưa context có evidence sang Deep.
- [x] `bots/decision_model/jira.py` nói rõ Nimble chỉ chọn gate label `use_jira_tool`/`ask_for_issue_key`/`skip_jira`, không tự gọi tool và không sinh reply tự do.
- [x] Không thêm chú thích vào các hàm hiển nhiên; chỉ sửa các docstring dễ gây hiểu nhầm.

## 4. Chính Sách Xóa/Giữ

Mục đích: dọn tài liệu cũ nhưng không làm mất lịch sử test có giá trị.

- [x] Không xóa file nào trong đợt này vì chưa có Markdown nào hoàn toàn trùng lặp và vô giá trị.
- [x] File cũ nhưng còn expected/result live test được giữ với nhãn historical.
- [x] Nếu sau này gộp hết nội dung historical vào verification/roadmap, có thể xóa file đó trong một commit riêng và cập nhật link tham chiếu.

## 5. Verification

Mục đích: đảm bảo thay đổi chỉ là docs/comment và không để lại cụm mô tả sai.

- [x] Chạy `rtk rg` cho các cụm stale về pending process-only, tên nút consolidation cũ, mô tả Loop chỉ mới dự kiến và mô tả consolidation chỉ thủ công; kết quả sau khi sửa không còn cụm đang gây hiểu nhầm.
- [x] Chạy `rtk git diff --check`.
- [x] Targeted Jira Decision Gate run: `rtk python -m pytest tests/test_decision_model.py tests/test_jira_tools.py tests/test_jira_issue_workflow.py tests/test_telegram_prompt.py tests/test_ops_dashboard.py -q` -> `95 passed`.
- [x] Chạy lại `rtk python -m pytest -q` sau khi hoàn tất Phase 6C docs -> `208 passed`.
