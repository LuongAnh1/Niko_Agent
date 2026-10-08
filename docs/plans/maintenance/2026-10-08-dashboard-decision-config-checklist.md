# Checklist Dashboard Decision Config

Ngày tạo: 2026-10-08
Tài liệu gốc: `docs/plans/maintenance/2026-10-08-dashboard-decision-config-plan.md`

## Phase 1: Ghi Nhận Thiết Kế

Mục đích: chốt rõ vì sao cần thêm tab con trong dashboard trước khi sửa UI/code.

- [x] Ghi nhận nhu cầu cấu hình confidence theo từng Decision Model surface.
- [x] Ghi nhận nhu cầu tách quyền mở tool ở `pre_deep_gate` và `deep_loop`.
- [x] Ghi rõ use case: pre-Deep không cho Decision Model mở tool, nhưng Deep loop
      vẫn có thể dùng tool read-only nếu policy cho phép.
- [x] Dẫn lại từ live test Jira/Fast gần nhất để biết vì sao thay đổi này cần thiết.

Tiêu chí hoàn thành:

- Người đọc biết đây là kế hoạch cấu hình dashboard, chưa phải code đã triển khai.
- Không còn hiểu nhầm confidence là một ngưỡng global áp dụng cho mọi quyết định.

## Phase 2: Tab Con Decision Confidence

Mục đích: gom toàn bộ ngưỡng tự tin của Decision Model/Python-assisted decision
vào một nơi dễ chỉnh và dễ giải thích.

- [ ] Thêm tab con `Decision Confidence` trong trang `Config`.
- [ ] Hiển thị `NIKO_FAST_TRIAGE_REPLY_CONFIDENCE_THRESHOLD` với help text rõ.
- [ ] Hiển thị `NIKO_JIRA_DECISION_CONFIDENCE_THRESHOLD` với help text rõ.
- [ ] Chuẩn bị slot cho memory retrieval/write/correction confidence threshold.
- [ ] Chuẩn bị slot cho sticker confidence threshold nếu sticker tiếp tục dùng
      Decision Model.
- [ ] Dashboard validation chặn giá trị ngoài khoảng `0.0 -> 1.0`.
- [ ] Trace/runtime log ghi `threshold` và hành động fallback khi confidence thấp.

Tiêu chí hoàn thành:

- Anh có thể chỉnh ngưỡng từng loại quyết định trên dashboard mà không phải mở
  `.env` hoặc nhớ tên key.
- Khi một quyết định bị fallback vì confidence thấp, dashboard giải thích được.

## Phase 3: Tab Con Tool Access Policy

Mục đích: kiểm soát tool theo domain và theo thời điểm dùng tool, tránh trộn
nhầm giữa workflow gate trước Deep với vòng lặp tool trong Deep.

- [ ] Thêm tab con `Tool Access Policy` trong trang `Config`.
- [ ] Tạo schema cho từng tool/domain: `jira.issue.read`, `memory.fact.read`,
      `memory.fact.update`, `memory.fact.delete`, và placeholder `jira.issue.write`.
- [ ] Mỗi tool có cấu hình riêng cho `pre_deep_gate`.
- [ ] Mỗi tool có cấu hình riêng cho `deep_loop`.
- [ ] Hỗ trợ mode `disabled`, `python_rule_only`, `decision_model_allowed`,
      `python_rule_or_decision`, `allowed_read_only`, `confirm_required`.
- [ ] Runtime đọc policy này trước khi workflow/Loop gọi tool.
- [ ] Mutating tool mặc định `confirm_required` hoặc `disabled`.

Tiêu chí hoàn thành:

- Có thể cấu hình: "Decision Model không được tự mở Jira tool trước Deep, nhưng
  Deep loop vẫn được đọc Jira nếu cần".
- Tool read-only và mutate có guardrail khác nhau.

## Phase 4: Kiểm Thử

Mục đích: đảm bảo config mới không làm route/tool chạy ngoài ý muốn.

- [ ] Unit test config schema cho confidence threshold.
- [ ] Unit test config schema cho tool policy mode.
- [ ] Test Fast triage `reply_now` confidence thấp bị đẩy sang Deep.
- [ ] Test Jira gate bị policy `pre_deep_gate=disabled` chặn trước Deep.
- [ ] Test Deep loop vẫn dùng được Jira read tool khi `deep_loop=allowed_read_only`.
- [ ] Test mutating tool không chạy nếu policy là `confirm_required`.
- [ ] Live test Telegram một prompt recent issue mơ hồ và đối chiếu runtime log.

Tiêu chí hoàn thành:

- Dashboard config quyết định đúng hành vi runtime.
- Trace/runtime log đủ để giải thích vì sao tool được mở hoặc bị chặn.

