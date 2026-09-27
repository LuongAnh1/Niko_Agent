# Jira Gateway Domain

## 1. Ý Tưởng

Jira Gateway là cổng nghiệp vụ dự kiến cho `Niko_Agent`.

Mục tiêu của gateway này là giúp Niko có thể đọc dữ liệu task/issue theo dự án,
thay vì chỉ chờ người dùng copy từng đoạn thông tin vào Telegram.

```text
Jira
  -> Jira Gateway
  -> normalized issue/task/comment/event records
  -> Memory Backend
  -> Niko Deep Agent analysis
```

## 2. Vì Sao Cần Jira Gateway

Khi phân tích một task hoặc issue, thông tin thường nằm rải rác:

- mô tả task;
- comment của nhiều người;
- changelog/status history;
- assignee/reporter;
- component/label/priority;
- issue link như duplicate/blocks/relates;
- log hoặc ghi chú xử lý;
- issue tương tự trong cùng project/component.

Nếu agent chỉ đọc một prompt hiện tại, nó không có đủ bối cảnh để phân tích tốt.
Jira Gateway giúp đưa dữ liệu nghiệp vụ vào harness theo cách có cấu trúc.

## 3. Nghiệp Vụ Jira Dự Kiến

Nghiệp vụ cơ bản:

```text
Một project Jira có nhiều issue/task.
Mỗi issue có description, comment, status, component, label, assignee và lịch sử thay đổi.
Agent cần đọc các dữ liệu này để phân tích tình trạng, nguyên nhân, liên quan và hướng xử lý.
```

Các câu hỏi mà Niko nên hỗ trợ trong tương lai:

- Issue này đang bị kẹt ở trạng thái nào?
- Comment nào chứa thông tin quan trọng nhất?
- Task này liên quan tới component nào?
- Có issue nào tương tự hoặc duplicate không?
- Ai từng xử lý loại lỗi này?
- Component này gần đây có pattern lỗi gì?
- Vì sao issue này bị reopen?
- Nên đọc issue/comment nào trước khi xử lý task mới?

## 4. Dữ Liệu Cần Đọc Từ Jira

| Dữ liệu | Ví dụ | Vai trò memory |
| --- | --- | --- |
| Project | key, name, lead | ngữ cảnh cấp dự án |
| Issue/Task | key, summary, description, type | semantic + entity trung tâm |
| Comment | author, body, created_at | semantic memory + timeline |
| Changelog | field, from, to, timestamp | episodic memory |
| Status | open, in progress, resolved, reopened | lifecycle analysis |
| Component | backend, sync-service, payment | phân nhóm kỹ thuật/nghiệp vụ |
| Label/Priority | bug, urgent, P1 | metadata phân tích |
| Actor | reporter, assignee, commenter | ai tham gia xử lý |
| Issue Link | blocks, duplicates, relates | graph relationship |
| Attachment/Log | optional | bằng chứng hoặc dữ liệu debug |

## 5. Sync Mode Dự Kiến

Phiên bản đầu không cần làm phức tạp. Có thể chia thành ba mức:

| Mức | Cách làm | Mục tiêu |
| --- | --- | --- |
| Manual import | nhập issue key hoặc file export | demo nhanh |
| Scheduled sync | đồng bộ định kỳ theo project/filter | có dữ liệu cập nhật |
| Webhook/event sync | nhận event khi Jira thay đổi | gần realtime |

Khuyến nghị v1:

```text
Manual import hoặc scheduled sync nhỏ
-> chuẩn hóa dữ liệu
-> ghi vào memory/lakehouse
-> cho Deep agent retrieve
```

## 6. Output Cho Agent

Jira Gateway không nên trả dữ liệu thô trực tiếp cho LLM. Nó nên chuẩn hóa thành
context có cấu trúc:

```text
Issue: PROJ-123
Summary: API returns 500 when syncing data
Component: sync-service
Status timeline:
  Open -> In Progress -> Resolved -> Reopened
Important comments:
  - comment id ...
Related issues:
  - PROJ-98 relates
  - PROJ-101 duplicates
Possible evidence:
  - resolution note ...
```

Sau đó memory/retrieval layer quyết định phần nào được đưa vào prompt của Deep
agent.

## 7. Ranh Giới V1

Trong `Niko_Agent` hiện tại, Jira Gateway mới là hướng nghiệp vụ dự kiến, chưa
phải phần đã implement.

V1 nên chỉ chốt về mặt thiết kế:

- Jira là cổng dữ liệu nghiệp vụ;
- dữ liệu Jira sẽ là nguồn Semantic/Episodic Memory quan trọng;
- dữ liệu cần được chuẩn hóa trước khi đưa vào LLM;
- memory backend/lakehouse/graph là lớp nâng cấp sau baseline SQLite.

