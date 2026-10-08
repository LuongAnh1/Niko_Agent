# Kế Hoạch Dashboard Decision Config

Ngày tạo: 2026-10-08
Trạng thái: ghi nhận yêu cầu thiết kế, chưa triển khai UI/code

## Mục Tiêu

Thêm các tab con trong trang `Config` của dashboard để cấu hình rõ các quyết
định đang do Python rule hoặc Decision Model đưa ra. Mục tiêu là tránh rải quá
nhiều ngưỡng và quyền mở tool trong `.env`/config dạng phẳng, đồng thời giúp
live test biết chính xác vì sao một turn được cho qua Fast, Deep, Jira tool,
memory tool hoặc bị fallback.

## Bối Cảnh

Trong live test Jira/Loop ngày 2026-10-08, có hai vấn đề lộ ra:

- Decision Model có thể chọn nhãn đúng/hợp lý nhưng confidence thấp, hoặc chọn
  `reply_now` với confidence quá thấp. Vì vậy ngưỡng confidence cần được chỉnh
  trực tiếp trên dashboard theo từng loại quyết định.
- Tool access hiện còn gộp giữa hai chuyện khác nhau: workflow trước Deep có
  được phép mở tool hay không, và trong lúc Deep chạy loop thì Deep có được mở
  tool nếu thấy cần hay không. Hai lớp này cần có cấu hình riêng.

## Tab Con 1: Decision Confidence

Mục đích: cấu hình ngưỡng tự tin theo từng surface quyết định thay vì dùng một
ngưỡng chung. Mỗi dòng cấu hình phải nói rõ nếu confidence thấp thì hệ thống sẽ
làm gì.

Các cấu hình cần gom vào tab con này:

| Surface | Config key | Trạng thái | Hành vi khi confidence thấp |
| --- | --- | --- | --- |
| Fast triage `reply_now/send_to_deep` | `NIKO_FAST_TRIAGE_REPLY_CONFIDENCE_THRESHOLD` | Đã có code/config field V0 | Nếu `reply_now` dưới ngưỡng thì override sang Deep |
| Jira decision gate | `NIKO_JIRA_DECISION_CONFIDENCE_THRESHOLD` | Đã có code/config field V0 | Không mở Jira workflow, fallback sang route chat thường hoặc hỏi lại key tùy label |
| Memory retrieval gate | `NIKO_MEMORY_RETRIEVAL_CONFIDENCE_THRESHOLD` | Đề xuất | Không retrieve hoặc dùng fallback bảo thủ |
| Memory write gate | `NIKO_MEMORY_WRITE_CONFIDENCE_THRESHOLD` | Đề xuất | Không ghi fact/episode dài hạn |
| Memory correction gate | `NIKO_MEMORY_CORRECTION_CONFIDENCE_THRESHOLD` | Đề xuất | Không mutate DB, hỏi lại hoặc bỏ qua correction |
| Sticker mood gate | `NIKO_STICKER_CONFIDENCE_THRESHOLD` | Đề xuất | Không gửi sticker nếu mood thiếu chắc |

Yêu cầu UI:

- Hiển thị theo nhóm quyết định, không để lẫn với API key/token.
- Mỗi input phải có help text: "dùng để làm gì", "thấp quá thì sao", "cao quá
  thì sao".
- Runtime log/trace cần ghi cả `confidence`, `threshold`, `label`, `provider`
  và `low_confidence_action` để debug từ dashboard.

## Tab Con 2: Tool Access Policy

Mục đích: cấu hình quyền mở tool theo từng domain/tool và theo từng thời điểm
trong luồng chạy.

Hai thời điểm cần tách rõ:

- `pre_deep_gate`: trước khi gọi Deep, Python rule hoặc Decision Model có thể
  quyết định đưa turn vào workflow có tool, ví dụ Jira issue workflow.
- `deep_loop`: trong lúc Deep/Loop đang xử lý, Deep có thể thấy thiếu dữ liệu và
  yêu cầu mở tool nếu policy cho phép.

Đề xuất model cấu hình:

| Tool domain/name | Pre-Deep gate | Deep loop | Ghi chú |
| --- | --- | --- | --- |
| `jira.issue.read` | `python_rule_or_decision` | `allowed_read_only` | Dùng cho đọc issue/comment/changelog fixture hoặc Jira API sau này |
| `memory.fact.read` | `python_rule_or_decision` | `allowed_read_only` | Cho inventory/retrieve fact |
| `memory.fact.update` | `confirm_required` | `confirm_required` | Mutating tool không tự chạy nếu thiếu xác nhận |
| `memory.fact.delete` | `confirm_required` | `confirm_required` | Mutating tool phải fail-closed |
| `jira.issue.write` | `disabled` | `disabled` | Chưa triển khai Jira bot/gateway thật |

Các mode nên hỗ trợ:

- `disabled`: không được mở tool ở lớp đó.
- `python_rule_only`: chỉ rule xác định chắc mới được mở.
- `decision_model_allowed`: Decision Model được quyền chọn mở tool khi đủ
  confidence.
- `python_rule_or_decision`: cả rule chắc và Decision Model đủ confidence đều
  được mở.
- `allowed_read_only`: Deep loop được gọi tool đọc dữ liệu.
- `confirm_required`: phải hỏi/xác nhận trước khi mutate.

Điểm anh vừa nêu cần được hỗ trợ trực tiếp:

```text
Pre-Deep gate không cho Decision Model tự mở tool,
nhưng trong quá trình Deep chạy loop, nếu Deep thấy cần dữ liệu
thì vẫn có thể mở tool read-only theo policy.
```

Ví dụ cấu hình tương ứng:

```text
jira.issue.read.pre_deep_gate = python_rule_only
jira.issue.read.deep_loop = allowed_read_only
```

## Guardrail

- Read-only tool có thể mở rộng trước, mutating tool phải giữ `confirm_required`
  hoặc `disabled`.
- Decision Model chỉ quyết định label/mode; Python vẫn validate issue key,
  fact ID, replacement và quyền tool trước khi thực thi.
- Mỗi lần policy chặn hoặc cho tool chạy phải có trace/runtime log riêng để khi
  live test không phải đoán.
- Dashboard nên giữ config này trong `niko/.runtime/config.json`; không đưa về
  `.env` trừ khi là bootstrap hoặc secret.

## Không Thuộc Phạm Vi Giai Đoạn Này

- Chưa tạo Jira bot/gateway thật.
- Chưa gọi Jira Cloud API thật.
- Chưa cho Deep tự mutate Jira hoặc memory fact nếu không có xác nhận.
- Chưa thay toàn bộ Loop controller bằng native LLM tool-use API.

