# Telegram Gateway Domain

## 1. Vai Trò Hiện Tại

Telegram hiện là cổng hội thoại realtime của `Niko_Agent`.

Nó cho phép người dùng:

- gửi câu hỏi hoặc yêu cầu cho Niko;
- gọi bot trong group bằng mention;
- nhận phản hồi nhanh từ local rule hoặc Fast agent;
- đẩy tác vụ khó sang Deep agent;
- xem phản hồi cuối cùng sau khi Deep xử lý xong.

Trong kiến trúc harness, Telegram không nên là nơi chứa logic phân tích. Nó chỉ
nên là gateway vào/ra.

```text
Telegram message
  -> bots/telegram
  -> ChatGatewayMessage
  -> ChatReplyGraph
  -> Reply
```

## 2. Dữ Liệu Telegram Đang Cung Cấp

| Nhóm dữ liệu | Ví dụ | Vai trò |
| --- | --- | --- |
| User identity | user id, username, alias | xác định ai đang chat |
| Conversation identity | chat id, chat type, chat title | gom lượt chat theo conversation |
| Message text | prompt người dùng gửi | input cho router/agent |
| Raw Telegram payload | message JSON gốc | debug khi cần |
| Reply metadata | route, status, trace id | quan sát luồng harness |

## 3. Nghiệp Vụ Telegram Phù Hợp Với Việc Gì

Telegram phù hợp với các tác vụ:

- hỏi nhanh;
- yêu cầu tóm tắt;
- yêu cầu phân tích một đoạn thông tin đã gửi;
- trao đổi follow-up khi Deep agent đang xử lý;
- demo luồng agent hoạt động thật;
- quan sát memory baseline qua dashboard.

Ví dụ:

```text
@Niko2_Bot em tóm tắt đoạn chat này giúp anh
@Niko2_Bot phân tích lỗi này có thể do đâu
@Niko2_Bot em có nhớ fact nào về dự án không
```

## 4. Giới Hạn Của Telegram Gateway

Telegram chỉ thấy những gì người dùng nhắn vào.

Nếu người dùng hỏi:

```text
Issue JIRA-123 đang bị kẹt ở đâu?
Component sync-service gần đây hay lỗi gì?
Comment nào trong Jira chứa hướng xử lý quan trọng?
```

thì Telegram gateway không tự có dữ liệu Jira để trả lời. Nếu không có gateway
nghiệp vụ hoặc memory backend, người dùng phải tự copy/paste từng đoạn dữ liệu
vào chat.

Đây chính là giới hạn:

```text
agent đọc dữ liệu rời rạc trong từng prompt
-> thiếu lịch sử
-> thiếu quan hệ
-> khó truy xuất nguồn
-> dễ trả lời thiếu căn cứ
```

## 5. Vai Trò Trong Memory

Telegram tạo ra dữ liệu runtime cho harness:

- `chat_log`: log hội thoại;
- `episodes`: sự kiện sau deep job;
- trace JSONL: route, retrieval, lỗi, kết quả;
- một số semantic facts nếu được thêm thủ công qua Ops.

Nhưng dữ liệu Telegram chưa đủ để đại diện cho nghiệp vụ doanh nghiệp. Nó cần
được kết hợp với gateway nghiệp vụ như Jira để agent có thể hiểu task, issue,
comment, component và lịch sử xử lý thật.

