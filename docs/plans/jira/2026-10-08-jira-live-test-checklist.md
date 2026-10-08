# Checklist Live Test Jira Runtime Tools

Ngày tạo: 2026-10-08

Tài liệu liên quan:

- [Kế hoạch Niko Loop](../loop/2026-10-08-niko-loop-implementation-plan.md)
- [Checklist Niko Loop](../loop/2026-10-08-niko-loop-implementation-checklist.md)
- [Jira Gateway Domain](../../business-domains/jira-gateway.md)
- [Telegram Chat Flow](../../harness/telegram-chat-flow.md)

## Mục Đích

Khóa phần test live cho Jira runtime tools qua Telegram và dashboard. Checklist này kiểm tra luồng:

```text
Telegram -> GatewayRunner -> NikoApp -> JiraIssueAnalysisWorkflow
  -> LoopRuntime -> Jira fixture tools -> Deep hoặc safe reply
```

Phạm vi hiện tại chỉ là Jira fixture read-only V0. Đây không phải Jira bot/gateway thật, không gọi Jira Cloud API thật và không ghi dữ liệu Jira vào chat memory SQLite mặc định.

## Chuẩn Bị

Mục đích: đảm bảo môi trường test live không bị nhiễu bởi bot chạy trùng, config cũ hoặc Decision Model chưa warmup.

- [ ] Dashboard đang chạy bằng `rtk python -m niko.ops.dashboard`.
- [ ] Telegram Bot được start từ tab `Bots`, không có bot chạy ngoài lock.
- [ ] `NIKO_JIRA_TOOLS_ENABLED=1`.
- [ ] `NIKO_JIRA_FIXTURE_PATH` để trống hoặc trỏ tới `niko/tools/jira/fixtures/demo_issues.json`.
- [ ] `NIKO_JIRA_LOOP_MAX_ITERATIONS=5`.
- [ ] `NIKO_TRACE_ENABLED=1` và `NIKO_RUNTIME_LOG_ENABLED=1`.
- [ ] Nếu test prompt Jira mơ hồ: bật `NIKO_JIRA_DECISION_GATE_ENABLED=1` và warmup Decision Model.
- [ ] Ghi lại thời điểm bắt đầu test để lọc `.runtime/traces` và Runtime Log.

Fixture demo hiện có key chính: `NIKO-101`, `NIKO-102`. Dùng `NIKO-404` hoặc một key không tồn tại để test no-data.

## Test 1: Clear Issue Key Happy Path

Mục đích: prompt có issue key rõ phải đi qua Jira workflow và Loop tools, không cần Jira Decision Gate.

- [x] Gửi Telegram: `@Niko2_Bot phân tích NIKO-101 giúp anh`.
- [x] Bot gửi wait reply nếu Deep chạy nền.
- [x] Bot trả lời cuối dựa trên dữ liệu fixture của `NIKO-101`.
- [x] Runtime Log/Dashboard `Bots` có `workflow_selected` với `workflow=jira_issue`.
- [x] Trace có các tool step `parse_issue_key`, `fetch_jira_issue`, `fetch_jira_comments`, `fetch_jira_changelog`.
- [x] Trace có `jira_issue_workflow_finished` với route Deep.
- [x] Prompt có key rõ không gọi Jira Decision Model; nếu có `jira_gate_decision` thì là `provider=python_rule`, `label=issue_key_in_prompt`.
- [x] Runtime Log có source `niko_app` event `workflow_selected`.

Kết quả live:

- Ngày/giờ: 2026-10-08 19:50-19:51 ICT.
- Trace ID: `d3891718-4f5e-4bbb-8899-cdaacdba923e`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: trace ghi `jira_gate_decision` bằng Python rule vì prompt đã có `NIKO-101`, sau đó Loop gọi đủ `parse_issue_key`, `fetch_jira_issue`, `fetch_jira_comments`, `fetch_jira_changelog`, route `jira_issue_deep_agent`, gửi wait reply và Deep trả phân tích cuối. Runtime Log có `niko_app.workflow_selected`; trace chính có `jira_issue_workflow_finished`. Checklist được chỉnh wording để phân biệt Python rule gate với Jira Decision Model và không nhầm tab khi tìm `workflow_selected`.

## Test 2: Missing Fixture Safe Reply

Mục đích: issue key hợp lệ về format nhưng không có trong fixture phải trả lời an toàn và không gọi Deep.

- [x] Gửi Telegram: `@Niko2_Bot phân tích NIKO-404 giúp anh`.
- [x] Bot trả lời rằng chưa có dữ liệu Jira fixture cho issue này.
- [x] Runtime Log/Dashboard `Bots` có `workflow_selected` với `workflow=jira_issue`.
- [x] Trace có `jira_issue_workflow_finished` với route `jira_issue_not_found`.
- [x] Không có `deep_agent_call_started` cho turn này.
- [x] Không có tool mutate state.

Kết quả live:

- Ngày/giờ: 2026-10-08 19:55 ICT.
- Trace ID: `92d09dfb-43de-4e9d-b829-d41b6e321c78`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: prompt có `NIKO-404` được Python rule đưa vào Jira workflow; Loop parse đúng key, `fetch_jira_issue` trả `issue_not_found`, `loop_final_answer` là `jira_issue_not_found`, `jira_issue_workflow_finished` có `has_deep_context=false`; runtime log có `niko_app.workflow_selected route=jira_issue_not_found`; không có Deep call cho trace này.

## Test 3: Feature Flag Off

Mục đích: khi tắt Jira tools, prompt có issue key không được tự ý chạy Jira workflow.

- [x] Tắt `NIKO_JIRA_TOOLS_ENABLED=0` trên dashboard và restart Telegram Bot nếu config yêu cầu.
- [x] Gửi Telegram: `@Niko2_Bot phân tích NIKO-101 giúp anh`.
- [x] Turn đi theo route chat bình thường, không có `workflow_selected workflow=jira_issue`.
- [x] Không có Jira loop tool call.
- [x] Bật lại `NIKO_JIRA_TOOLS_ENABLED=1` sau khi test xong.

Kết quả live:

- Ngày/giờ: 2026-10-08 19:57-19:58 ICT.
- Trace ID: `c1ab8502-1fe3-4768-a243-de0c9c632dc6`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: runtime config xác nhận lúc test `NIKO_JIRA_TOOLS_ENABLED=0`; turn đi `normal_chat -> deep_agent`, không có `jira_gate_decision`, không có `parse_issue_key`/`fetch_jira_issue`, không có `jira_issue_workflow_finished`. Deep vẫn trả lời được nhờ recent working-memory window (`memory_retrieval` có `recent_turn_count=6`), và chính reply cũng nói chưa được nạp lại context Jira fixture. Sau test đã bật lại `NIKO_JIRA_TOOLS_ENABLED=1`.

## Test 4: Ambiguous Prompt Có Recent Issue

Mục đích: kiểm tra Jira Decision Gate default-off khi bật lên có dùng recent chat làm ngữ cảnh, nhưng Python vẫn validate issue key trước khi gọi tool.

- [x] Bật `NIKO_JIRA_DECISION_GATE_ENABLED=1`.
- [x] Warmup Decision Model.
- [x] Trước đó trong cùng chat đã nhắc rõ `NIKO-101`.
- [x] Gửi Telegram: `@Niko2_Bot xem ticket vừa nãy giúp anh`.
- [x] Trace có `jira_gate_decision`.
- [x] Gate chọn `use_jira_tool` và workflow dùng issue key đã xuất hiện trong recent chat: `NIKO-101`.
- [x] Nhánh hỏi lại issue key không chạy trong retest này vì Python rule đã đủ chắc.
- [x] Không fetch issue bằng key không có trong current/recent prompt.

Kết quả live:

- Ngày/giờ: 2026-10-08 20:02 ICT.
- Trace ID: `7bf589c7-6e0d-410d-b5ad-b0bacb6a93c3`.
- Kết luận: Fail lần 1, đã sửa code và cần live retest.
- Ghi chú/sửa đổi đi kèm: Jira Decision Gate đã thấy recent issue `NIKO-101` nhưng Nimble trả `skip_jira` với `reason=confidence_below_threshold`, làm turn rơi về `normal_chat -> fast_agent/deep_agent` thay vì Jira workflow. Đã thêm rule bảo thủ trong `JiraIssueAnalysisWorkflow`: nếu current prompt tham chiếu rõ issue gần nhất bằng cụm như `ticket vừa nãy` và recent turns có issue key, workflow dùng `python_rule` với `reason=recent_issue_reference` trước khi gọi Nimble. Thêm test hồi quy `test_recent_issue_reference_uses_python_rule_before_model`; pytest hẹp `tests/test_jira_issue_workflow.py tests/test_jira_tools.py` pass 18/18.
- Follow-up 2026-10-08 20:18 ICT: prompt `cái vừa rồi có đang bị block không em?` không khớp rule bảo thủ, Jira gate gọi Nimble và fallback vì confidence thấp; sau đó Fast triage cũng không có recent working memory nên trả lời mù ngữ cảnh. Đã sửa Fast triage/Fast reply để nhận `recent_turns` từ `chat_log`; thêm test `test_decision_model_fast_triage_receives_recent_turns` và `test_triage_state_includes_recent_turns`.
- Follow-up 2026-10-08 20:52 ICT: sau khi Fast đã nhận `recent_turn_count=6`, Nimble vẫn chọn `reply_now` với confidence rất thấp (`0.017`) và Fast reply nói chưa đủ context. Đã thêm guardrail `NIKO_FAST_TRIAGE_REPLY_CONFIDENCE_THRESHOLD=0.65`: `reply_now` dưới ngưỡng bị override sang Deep; thêm test `test_low_confidence_reply_now_handoffs_to_deep`.
- Ghi chú cấu hình tiếp theo: cần thêm tab con trong dashboard cho các ngưỡng confidence theo từng Decision Model surface, và thêm tab con `Tool Access Policy` để tách quyền mở tool ở lớp `pre_deep_gate` với quyền dùng tool trong `deep_loop`. Kế hoạch/checklist nằm ở `docs/plans/maintenance/2026-10-08-dashboard-decision-config-plan.md` và `docs/plans/maintenance/2026-10-08-dashboard-decision-config-checklist.md`.
- Retest 2026-10-08 21:28-21:29 ICT: prompt `xem ticket vừa nãy giúp anh` pass. Trace ID `6b2d5d04-4f3c-4369-9885-d4e969920915`. `jira_gate_decision` dùng `provider=python_rule`, `label=recent_issue_reference`, chọn `issue_key=NIKO-101` từ recent context. Loop gọi đủ `parse_issue_key`, `fetch_jira_issue`, `fetch_jira_comments`, `fetch_jira_changelog`; tất cả `mutates_state=false`. `jira_issue_workflow_finished` route `jira_issue_deep_agent`, `deep_extra_context` có `1244` chars, `turn_end` status `ok`. Kết luận: recent issue reference đã vào đúng Jira workflow và Deep trả lời dựa trên context fixture.

## Test 5: Ambiguous Prompt Không Có Recent Issue

Mục đích: prompt mơ hồ không có issue key và không có recent issue hợp lệ không được gọi tool bằng dữ liệu bịa.

- [x] Dùng chat mới hoặc đảm bảo recent turns không có issue key.
- [x] Gửi Telegram: `@Niko2_Bot xem ticket giúp anh`.
- [x] Bot hỏi lại issue key hoặc bỏ qua Jira để đi route chat bình thường.
- [x] Trace có `jira_gate_decision` nếu prompt đủ tín hiệu Jira/task.
- [x] Không có `fetch_jira_issue` với key tự bịa.

Kết quả live:

- Ngày/giờ: 2026-10-08 21:42-21:43 ICT.
- Trace ID: `093b8dae-b660-480f-98e7-f0e1175d2f19`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: test chạy trong private chat `conversation_id=7576909312`, prompt `Xem ticket giúp anh` không có issue key và không có recent issue trong chat này. `jira_gate_decision` gọi Nimble, trả `decision=ask_for_issue_key`, `issue_keys=[]`, `issue_key=""`, confidence `0.347` dưới ngưỡng nên không mở Jira workflow. Turn đi `normal_chat -> fast_agent`; không có `parse_issue_key`, `fetch_jira_issue`, `fetch_jira_comments`, `fetch_jira_changelog`. Reply cuối yêu cầu anh gửi thông tin ticket hoặc nạp dữ liệu ticket, không bịa key.

## Test 6: Non-Jira Prompt Không Bị Cướp Luồng

Mục đích: bật Jira tools/gate không làm prompt thường bị đưa nhầm sang workflow Jira.

- [x] Gửi Telegram một prompt không liên quan Jira, ví dụ: `@Niko2_Bot hiện tại em nhớ gì về anh?`
- [x] Turn không có `workflow_selected workflow=jira_issue`.
- [x] Không có Jira loop tool call.
- [x] Memory gate hoặc normal chat vẫn hoạt động theo config hiện tại.

Kết quả live:

- Ngày/giờ: 2026-10-08 21:36-21:37 ICT.
- Trace ID: `23ce7160-b24a-47b8-8fe6-6b65cb6dad44`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: prompt `hiện tại em nhớ gì về anh?` đi `normal_chat -> deep_agent`, không có `jira_gate_decision`, không có `workflow_selected workflow=jira_issue`, không có `parse_issue_key`/`fetch_jira_issue`/`fetch_jira_comments`/`fetch_jira_changelog`. Memory retrieval vẫn hoạt động với `gate_decision=retrieve`, `fact_mode=list`, `fact_ids=[13, 7]`, `recent_turn_count=6`; bot trả lời bằng memory/context thường chứ không bị Jira gate cướp luồng.

## Test 7: Read-Only Boundary

Mục đích: xác nhận Jira lane V0 chỉ đọc dữ liệu, chưa mutate Jira và chưa phải Jira bot.

- [x] Gửi Telegram: `@Niko2_Bot đổi status NIKO-101 sang done giúp anh`.
- [x] Workflow có thể fetch context của `NIKO-101`.
- [x] Bot không được khẳng định đã đổi status trên Jira.
- [x] Trace chỉ có Jira read tools, không có mutate tool.
- [x] Nếu Deep trả lời nhập nhằng như đã đổi trạng thái, ghi fail và cần sửa prompt guardrail.

Kết quả live:

- Ngày/giờ: 2026-10-08 21:39-21:40 ICT.
- Trace ID: `a08db80b-4a7a-4240-9178-c7890f136642`.
- Kết luận: Pass.
- Ghi chú/sửa đổi đi kèm: prompt `đổi status NIKO-101 sang done giúp anh` có issue key rõ nên `jira_gate_decision` dùng `provider=python_rule`, `label=issue_key_in_prompt`, route vào `jira_issue_deep_agent`. Loop gọi đủ `parse_issue_key`, `fetch_jira_issue`, `fetch_jira_comments`, `fetch_jira_changelog`; các tool đều `mutates_state=false`. `deep_extra_context` có `1244` chars. Reply cuối nói rõ Jira context hiện là read-only fixture nên chỉ xem được, không thể đổi status; bot không khẳng định đã mutate Jira.

## Tiêu Chí Khóa Live Test

Mục đích: biết rõ khi nào có thể coi Jira runtime tools V0 đã đủ ổn để demo.

- [x] Clear issue key đi đúng Jira workflow và hiển thị Loop Steps trên dashboard.
- [x] Missing fixture trả safe reply và không gọi Deep.
- [x] Feature flag off không chạy Jira workflow.
- [x] Ambiguous prompt không bịa issue key.
- [x] Non-Jira prompt không bị Jira gate cướp luồng.
- [x] Read-only boundary không bị bot nói là đã sửa Jira.
- [ ] Tài liệu liên quan được cập nhật sau mỗi lỗi/sửa đổi phát hiện khi live test.
