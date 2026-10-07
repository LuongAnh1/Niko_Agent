# Kế Hoạch Chat Memory Decision Model

Ngày lập: 2026-10-07
Loại tài liệu: kế hoạch triển khai ngắn hạn
Phạm vi: chat memory single-user trong `Niko_Agent`

Checklist triển khai: [2026-10-07-chat-memory-decision-model-checklist.md](2026-10-07-chat-memory-decision-model-checklist.md)

Tài liệu này chỉ nói về memory trong chat/tương tác người dùng của Niko. Nó
khác với tầng memory nghiệp vụ/lakehouse cho Jira. Jira và tài liệu nghiệp vụ
nằm ở lane riêng trong repo `Ai-Memory-Lakehouse-Graph-Mining`; kế hoạch này chỉ
tập trung vào việc Niko nhớ người dùng, cuộc trò chuyện, preference, fact và
episode tốt hơn trong Telegram.

Ý chính sau khi đọc Waku: memory tốt không chỉ là “lưu thêm dữ liệu”. Nó cần
một lớp quyết định nhẹ để biết lúc nào nên nhớ, lúc nào nên đọc lại, lúc nào nên
bỏ qua, và lúc nào user đang yêu cầu sửa/quên memory. Với Niko, lớp này nên dùng
local Ollama/Nimble decision model đang có, thay vì đẩy mọi quyết định nhỏ sang
Deep agent.

Quyết định scope cho v1:

```text
V1: single-user chat memory
  facts/episodes là memory chung của một Niko instance
  chat_log vẫn giữ session_id/conversation_id để xem lịch sử hội thoại
  retrieval chưa filter theo user_key
  lakehouse/Jira không nằm trong chat memory mặc định

V2: scoped memory
  thêm owner/scope vào facts/episodes
  scope = global | user | conversation | project
  retrieval merge theo current user/conversation/project
  Jira/lakehouse là business memory backend riêng
```

V1 single-user không phải thiếu sót, mà là constraint chủ động để hệ thống không
bị phức tạp trước khi retrieval gate, write gate, consolidation và correction
đủ quan sát được.

Nguồn tham chiếu chính từ Waku:

- `waku/runtime/session.py`
- `waku/memory/__init__.py`
- `waku/memory/retrieval_gate.py`
- `waku/memory/consolidation.py`
- `waku/memory/semantic/store.py`
- `waku/memory/episodic/store.py`
- `waku/tools/memory_admin.py`
- `waku/db.py`
- `waku/docs/architecture.md`
- `waku/docs/tour.md`

## Waku Làm Memory Như Thế Nào

Waku chia memory thành hai vùng rõ ràng:

```text
Ephemeral working memory mỗi turn
  SOUL.md
  thời gian hiện tại + model/provider identity
  memory context đã retrieve
  skill instructions phù hợp
  history window gần nhất
  message mới của user

Long-term memory bền vững
  chat_log
  semantic facts
  episodic events
  procedural skills
```

Điểm quan trọng là working memory được dựng lại mỗi turn rồi bỏ đi. Cái tồn tại
lâu dài nằm trong database/file memory. Thiết kế này giúp prompt hiện tại không
phình vô hạn, nhưng vẫn có đường retrieve lại thông tin cũ khi cần.

### Semantic, Episodic, Procedural

Waku có ba nhóm memory:

| Nhóm | Nơi lưu | Ý nghĩa |
| --- | --- | --- |
| Semantic | `facts` + FTS5 | fact bền vững về người dùng, người liên quan, dự án, preference |
| Episodic | `episodes` + FTS5 | sự kiện đã xảy ra, có ngày/thời điểm |
| Procedural | `SKILL.md` | cách làm một workflow lặp lại |

Niko hiện đã có semantic/episodic baseline, nhưng chưa có procedural memory
theo nghĩa skill runtime cho chính Niko.

Điểm quan trọng về scope: Waku local giả định `one user, one machine`. `chat_log`
có `session_id` để đổi khung chat/lịch sử hội thoại, nhưng `facts` và `episodes`
là memory chung trong một `state.db`, không tách theo từng khung chat. Cách này
hợp với v1 của Niko nếu mình xem mỗi Niko instance là bot cá nhân/local demo.

### Retrieval Gate

Waku không search memory ở mọi turn. Trước khi đụng store, một model nhỏ trả lời
một câu hỏi hẹp:

```text
Tin nhắn này có cần memory của user để trả lời tốt không?
```

Output là JSON:

```json
{"retrieve": true, "query": "Alex meeting", "reason": "asks about a person"}
```

Nếu `retrieve=false`, Waku bỏ qua search memory. Nếu `retrieve=true`, Waku dùng
`query` đó để search facts và episodes.

Tư duy đáng lấy:

- Retrieval không nên default-on, vì vừa chậm vừa dễ kéo memory không liên quan
  vào prompt.
- Gate dùng model nhỏ và task rất hẹp.
- Gate lỗi thì fail-open: retrieve bằng raw user message. Memory cũ có thể
  nhiễu, nhưng tắt nhầm memory khi user thật sự cần nhớ còn tệ hơn.
- Gate decision phải hiện trong trace/dashboard để thấy tại sao memory được dùng
  hoặc bị bỏ qua.

### Search Store

Waku dùng SQLite FTS5 cho local semantic/episodic memory. Họ rất chú ý phần query
builder:

- Không dùng raw user text làm FTS query.
- Tách token theo Unicode, không chỉ ASCII.
- Diacritics/non-Latin phải còn search được.
- Query rỗng với facts trả `[]`, không trả bừa mọi fact.
- Với CJK/unsegmented scripts, dùng prefix cho token phù hợp.

Điểm này đáng chú ý cho Niko vì người dùng chat tiếng Việt. Nếu query builder
không đồng nhất với tokenizer của SQLite, memory có thể bị miss hoặc trả nhầm.

### Consolidation

Waku không biến mọi message thành fact ngay. Nó log chat trước, rồi chỉ
consolidate khi đủ N exchange.

```text
chat_log unconsolidated
  -> đủ N exchange?
  -> summarizer nhỏ đọc batch
  -> xuất durable facts + một episode
  -> ghi facts/episodes
  -> mark đúng các row đã đọc là consolidated
```

Các rule quan trọng:

- Dưới threshold thì không gọi model, để tiết kiệm cost/latency.
- Summarizer lỗi hoặc output không parse được thì không mark row đã consolidate.
  Log vẫn còn đó để thử lại lần sau.
- Chỉ mark đúng những row đã đọc. Nếu message mới tới trong lúc summarize, nó
  vẫn phải ở trạng thái unconsolidated.
- Small talk có thể không tạo fact nào, nhưng vẫn có thể mark done nếu model trả
  JSON hợp lệ và nói không có gì đáng nhớ.
- Fact có provenance `source=consolidation`; fact người dùng nhập tay nên là
  `manual` hoặc `user`.

### Agent Tự Quản Lý Memory

Waku có tool `manage_memory` để agent search/update/delete facts hoặc episodes
khi user nói “cái đó sai”, “quên cái này đi”. Nó yêu cầu search trước để lấy ID,
rồi mới update/delete. Đây là khác biệt lớn so với chỉ có dashboard CRUD.

Waku còn có:

- `update_soul`: append standing behavior rule vào `SOUL.md`.
- `create_skill`: tạo một `SKILL.md` mới cho workflow lặp lại.

Niko hiện chưa có tool loop hoàn chỉnh, nên chưa nên bê nguyên phần này. Nhưng
về mặt UX, Niko nên có một đường “sửa/quên memory” qua chat hoặc qua dashboard
rõ ràng hơn.

### Human-Readable Mirror

Waku giữ source of truth trong `state.db`, nhưng sau mỗi turn regenerate
`.waku/MEMORY.md`. Đây không phải store chính, mà là bản mirror dễ đọc. Nó giúp
người dùng tin hệ thống hơn vì “memory là file mình mở được”.

Niko có thể học ý tưởng này ở mức nhẹ: xuất một file Markdown snapshot từ
`facts` và `episodes` trong `niko/.runtime/`, không dùng file đó làm nguồn truth.

## Niko Hiện Đang Ở Đâu

Niko hiện đã có:

- `chat_log`: operational conversation log.
- `facts`: semantic memory thủ công/baseline.
- `episodes`: episodic memory sau deep job.
- FTS5 nếu có, fallback LIKE.
- Deep agent được inject memory context.
- Ollama/Nimble decision model đã dùng cho fast triage, sticker mood, retrieval gate và write gate.
- Fast triage không nhận memory context để giữ route decision sạch.
- Dashboard Memory tab để thêm/xóa fact và xem episodes.
- V1 đang hợp với giả định single-user: một Niko instance phục vụ một chủ sở hữu
  chính.

Những phần còn thiếu hoặc mới ở mức scaffold so với Waku:

- Đã có retrieval gate v1, default-off, dùng Nimble để chọn `skip/retrieve` khi bật.
- Đã có write gate v1, default-off, hiện chỉ quyết định ghi/bỏ `episodes`.
- Đã có manual consolidation v1: đọc batch `chat_log`, tạo candidate bảo thủ,
  phân loại `semantic_fact` / `episodic_event` / `discard`, ghi facts/episodes và mark batch.
- Chưa có threshold/scheduler tự động cho consolidation.
- Đã có memory correction qua chat ở mức V1 tạm thời: intent gate, hỏi lại khi
  mơ hồ, update/delete fact có trace. Workflow bền hơn nên chuyển sang Loop/tool
  ở phase sau.
- Chưa có working-memory model rõ: recent history window, session switch/reload.
- Chưa có readable `MEMORY.md` mirror.
- Chưa có eval scenario riêng cho consolidation/correction; unit tests cho gate/search đã có.
- Episode hiện là summary của deep job, chưa phải event taxonomy giàu nghĩa.

Những phần cố ý chưa làm ở v1:

- Chưa tách facts/episodes theo từng `user_key` hoặc từng group chat.
- Chưa nối mặc định sang lakehouse/Jira memory backend.
- Chưa làm phân quyền memory nhiều người dùng.

Các phần này để v2 vì chúng kéo theo scope merge, permission, conflict
resolution và UI debug phức tạp hơn.

## Ranh Giới Với Lakehouse/Jira

Lakehouse/Jira memory backend là một lane khác:

```text
Public Jira / tài liệu / issue comment / changelog
  -> lakehouse Bronze/Silver/Gold
  -> Semantic/Episodic records nghiệp vụ
  -> Knowledge Graph / Graph Mining
  -> retrieval context có nguồn khi user hỏi về issue/project
```

Chat memory của Niko là lane local:

```text
Telegram conversation
  -> chat_log / facts / episodes
  -> Decision Model gates
  -> Deep prompt memory context
```

Hai lane có thể nối nhau sau này qua tool/retrieval slot, nhưng không nên dùng
lakehouse làm nơi lưu mặc định cho mọi tin nhắn Telegram.

## Decision Model Trong Chat Memory

Niko nên xem local Ollama/Nimble là lớp `System One`: nhanh, hẹp, trả label, và
giúp graph quyết định nhánh xử lý. Nó không thay Deep agent, không tự sinh câu
trả lời dài, và không tự ghi/sửa memory tùy ý.

Các decision point phù hợp:

| Decision point | Trạng thái | Câu hỏi hẹp | Output mong muốn |
| --- | --- | --- | --- |
| `route_triage` | Đã có | Prompt này trả nhanh được không hay cần Deep? | `reply_now` hoặc `send_to_deep` |
| `sticker_mood` | Đã có | Reply này có nên thả sticker không? Mood nào? | `no_sticker` hoặc mood hợp lệ |
| `memory_retrieval_gate` | Đã có v1, default-off | Turn này có cần đọc long-term memory không? | `skip` hoặc `retrieve` + query |
| `memory_write_gate` | Đã có v1, default-off | Turn này có thông tin đáng nhớ không? | `discard` hoặc `remember` |
| `memory_type_classifier` | Đã có v1 | Memory ứng viên là fact, episode hay không nên lưu? | `semantic_fact`, `episodic_event`, `discard` |
| `memory_correction_intent` | Đã có V1 tạm | User đang yêu cầu sửa/quên memory không? | `none`, `correct_memory`, `forget_memory` |

Pattern chung nên dùng:

- Mỗi task có `state` nhỏ, chỉ gồm dữ liệu cần thiết cho quyết định đó.
- Mỗi task dùng `systemone_choice(...)` trong `bots/decision_model/client.py`.
- Criteria phải là label đóng, không yêu cầu model viết văn dài.
- Decision model có thể trả `confidence`, `probabilities`, `model`, `usage`; các
  trường này nên đi vào trace/runtime log.
- Lỗi decision model không được làm hỏng turn chính. Retrieval gate fail-open,
  write/correction gate fail-safe.

Ranh giới quan trọng:

- Telegram gateway không gọi memory decision trực tiếp.
- Deep agent không tự gọi decision model.
- `ChatReplyGraph` điều phối flow; logic task decision nên nằm trong
  `bots/decision_model/` hoặc wrapper mỏng ở `niko/memory/`.
- SQLite vẫn là source of truth. Decision model chỉ quyết định nhánh và đề xuất
  query/label.

## Nguyên Tắc Nên Áp Dụng Cho Niko

1. `chat_log` không phải semantic/episodic memory. Nó là nguyên liệu để đọc lại
   hoặc consolidate.
2. Retrieval nên có gate, không search memory ở mọi câu Deep.
3. Write/consolidation nên có gate, không tự động nhớ mọi small talk.
4. Retrieval gate lỗi thì fail-open; write/correction/consolidation lỗi thì
   fail-safe, không mất log và không ghi bừa.
5. Memory nào được agent tự suy ra phải có provenance khác memory user nhập tay.
6. Fast/Nimble triage vẫn không nhận memory context.
7. Mọi quyết định memory phải trace được: gate decision, query, facts/episodes
   retrieved, consolidation result, errors.
8. Người dùng phải có cách sửa/quên memory.

## Plan Cải Tiến Đề Xuất

### Phase 1: Memory Decision Layer

Trạng thái: đã triển khai `memory_retrieval_gate`, `memory_write_gate`,
`memory_type_classifier` và `memory_correction_intent` v1. Correction hiện là
lớp tạm trong chat runtime; phase Loop/tool sẽ cần thay bằng workflow rõ hơn.

Mục tiêu: chuẩn hóa cách Niko dùng local Ollama/Nimble cho các quyết định nhỏ
trong memory pipeline.

Thêm một package memory-decision dùng chung:

```text
bots/decision_model/memory/
```

Package này dùng lại `systemone_choice(...)` và cung cấp các hàm hẹp:

- `decide_memory_retrieval(...)` đã có.
- `decide_memory_write(...)` đã có.
- `classify_memory_candidate(...)` đã có.
- `decide_memory_correction_intent(...)` đã có V1 tạm.

Các hàm này chỉ trả dataclass/metadata quyết định, không search store và không
ghi database. Caller ở graph/memory pipeline quyết định hành động tiếp theo.

Config nên bắt đầu nhỏ:

- `NIKO_MEMORY_GATE_ENABLED`
- `NIKO_MEMORY_WRITE_GATE_ENABLED`
- `NIKO_MEMORY_CORRECTION_DETECTION_ENABLED`
- timeout riêng có thể kế thừa `NIKO_DECISION_MODEL_TIMEOUT_SECONDS` trước, chỉ
  tách config riêng khi thực tế cần.

Trace/runtime log:

- `memory_gate_decision`
- `memory_write_decision`
- `memory_type_decision`
- `memory_correction_decision`
- `memory_decision_error`

Scope v1: các decision này chạy trên memory chung của một Niko instance. Chưa
cần truyền `user_key` làm partition bắt buộc; chỉ đưa vào trace/meta để sau này
mở rộng scope dễ hơn.

### Phase 2: Retrieval Gate Cho Deep

Trạng thái: đã triển khai v1, default `NIKO_MEMORY_GATE_ENABLED=0`.

Mục tiêu: giảm retrieval thừa và làm memory path quan sát được hơn.

Luồng:

```text
Deep candidate prompt
  -> memory_retrieval_gate
     -> skip
     -> retrieve with query
     -> fail-open retrieve raw prompt
  -> retrieve_memory_context
  -> build Deep prompt
```

Test cần có:

- Gate `skip` thì không gọi store search.
- Gate `retrieve` dùng query do gate trả.
- Gate lỗi thì retrieve bằng raw prompt.
- Trace có label, confidence, query và reason.
- Inventory question kiểu “đang lưu fact nào” vẫn đi qua gate; Decision Model
  trả choice `list_facts` hoặc `fact_mode=list` để runtime list memory thay vì
  search theo chữ `fact`.

### Phase 3: Unicode/Query Search Hardening

Mục tiêu: search tiếng Việt và query lạ không trả sai.

Niko nên rà lại `_fts_query` trong `niko/memory/store.py`:

- Giữ token Unicode thay vì chỉ ASCII.
- Query rỗng không được trả memory bừa.
- Có test cho tiếng Việt có dấu.
- Có test cho query toàn punctuation.
- Có test chống prefix quá rộng kiểu `car` match `carpet` nếu không chủ ý.

Phase này nhỏ nhưng đáng làm sớm vì Niko chat tiếng Việt là chính.

### Phase 4: Write Gate Và Chat Consolidation

Mục tiêu: tự tạo semantic facts/episodes từ chat thật, nhưng không spam memory.

Trạng thái: write gate v1 đã có; manual consolidation v1 đã có cột `consolidated`,
batch preview, candidate builder bảo thủ, memory type classifier, ghi facts/episodes
với `source=consolidation`, và dashboard/API trigger thủ công. Threshold/scheduler
tự động và summarizer tự do vẫn để phase sau.

Luồng đề xuất:

```text
Sau turn hoàn tất
  -> memory_write_gate
     -> discard: chỉ giữ chat_log
     -> remember: đưa vào batch consolidation
  -> nếu đủ N exchange đáng nhớ trong instance này
  -> candidate builder bảo thủ tạo candidates
  -> memory_type_classifier lọc fact/episode/discard
  -> ghi facts/episodes
  -> mark đúng row đã đọc là consolidated
```

Thêm vào `chat_log`:

```text
consolidated INTEGER DEFAULT 0
```

Thêm module:

```text
niko/memory/consolidation.py
```

Config:

- `NIKO_MEMORY_CONSOLIDATION_ENABLED`
- `NIKO_MEMORY_CONSOLIDATE_EVERY_N`
- `NIKO_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS`

Các config này dành cho auto consolidation phase sau; manual consolidation hiện
chạy qua dashboard/API và chưa có scheduler nền.

V1 consolidation đọc chat log local của một Niko instance. Khi mở rộng multi-user
mới cần thêm scope filter theo `user_key` hoặc `conversation_id`.

Test cần có:

- Dưới threshold không gọi model.
- Write gate `discard` không tạo fact/episode.
- Model lỗi không mark consolidated.
- Output không parse được không mark consolidated.
- Rows mới tới trong lúc consolidate không bị mark.
- Fact thiếu subject/content bị bỏ.
- No facts hợp lệ vẫn có thể mark done để không retry small talk mãi.

### Phase 5: Memory Correction Qua Chat/Dashboard

Mục tiêu: user có thể nói “cái đó sai”, “quên cái này đi” và Niko xử lý rõ
ràng.

Trạng thái: đã triển khai V1 tạm thời cho chat memory local. Vì Niko chưa có
tool loop hoàn chỉnh, phần này cố ý nhẹ: Decision Model chỉ nhận diện intent,
Python giữ pending fact IDs trong RAM, validate lựa chọn, rồi update/delete
SQLite có trace. Khi Loop/tool slot hoàn chỉnh hơn, luồng này nên chuyển thành
workflow/tool có state bền, thay vì tiếp tục mở rộng pending logic trong chat.

```text
Incoming prompt
  -> memory_correction_intent
     -> none: đi flow chat bình thường
     -> correct_memory/forget_memory:
        -> search memory liên quan
        -> nếu đủ chắc chắn: update/delete có trace
        -> nếu mơ hồ: hỏi lại user hoặc đẩy Deep giải thích
```

- Dashboard: thêm update fact, delete episode nếu cần.
- Chat command hoặc intent đơn giản:
  - “quên fact về X”
  - “sửa fact #id thành ...”
- Deep prompt có thể được hướng dẫn: nếu user yêu cầu sửa/quên memory, trả về
  một dạng structured instruction hoặc route riêng, chưa để model tự sửa tự do.

Không nên cho Deep tự ghi/sửa memory tùy ý ở bước đầu.
Live Phase 5 V1 đã xác nhận lệnh quên mơ hồ hỏi lại, follow-up chọn `fact #...`
xóa đúng record, và lệnh sửa mơ hồ không mutate dữ liệu. Các test update
end-to-end sau khi chọn ID được defer cho Loop/tool workflow.

### Phase 6: Working Memory Rõ Ràng Hơn

Mục tiêu: Niko phân biệt ba thứ:

- current prompt,
- recent conversation window,
- retrieved long-term memory.

Trạng thái: đã triển khai v1. Deep prompt có recent conversation window lấy từ
`chat_log`, budget riêng cho recent/long-term context, section long-term rõ và
label `Current user message` cho prompt hiện tại.

Các section hiện dùng:

- identity context từ `niko.chat_gateway`,
- `Recent conversation`,
- `Relevant semantic facts`,
- `Relevant episodic events`,
- `Current user message`.

Trace `memory_retrieval` có `recent_turn_count` để debug Deep có nhận working
memory hay không. Retrieval gate chỉ quyết định long-term memory; gate `skip`
không chặn recent conversation.

Trong v1, `conversation_id` chỉ dùng cho recent chat window và Deep job lock.
Long-term facts/episodes vẫn là memory chung của instance.

### Phase 7: Eval Cho Chat Memory

Mục tiêu: memory không chỉ “có vẻ chạy”, mà có test chứng minh.

Nên có deterministic tests:

- save fact, hỏi trực tiếp, retrieve đúng.
- hỏi câu không cần memory, gate skip.
- gate lỗi, retrieve fail-open.
- write gate discard, không tạo memory dài hạn.
- consolidation below threshold không gọi model.
- consolidation failure không mất log.
- query tiếng Việt có dấu search đúng.
- correction/delete fact không còn retrieve fact cũ.

Sau đó mới thêm judge/eval mềm cho chất lượng trả lời.

## Khuyến Nghị Bước Tiếp Theo Cho Niko

Các bước đầu đã hoàn thành: `bots/decision_model/memory/` đã tách package,
correction workflow đã tách khỏi `MemoryRuntime`, retrieval/write/candidate/correction
gates đã có test và trace, search tiếng Việt đã được harden, manual consolidation
đã chạy được qua dashboard/API, và Deep prompt đã có recent working-memory window.

Em đề xuất bước tiếp theo nên là hai việc nhỏ, ít rủi ro:

1. Thêm eval prompt mẫu cho retrieval/write/correction thay vì chỉ live test thủ công.
2. Sau eval, cân nhắc threshold/scheduler consolidation tự động nếu demo cần.

Lý do: correction và working memory đã có baseline quan sát được; trước khi mở
thêm scheduler hoặc Loop/tool bền, mình cần eval để tránh cảm giác "có vẻ chạy"
nhưng thiếu regression.

Không triển khai trong bước đầu:

- multi-user memory scope;
- lakehouse/Jira retrieval;
- graph schema cho facts/episodes;
- quyền truy cập memory theo group/user.

## Những Gì Không Nên Bê Nguyên Từ Waku

- Không cần provider abstraction/memory backend arena ngay.
- Không cần procedural skills ngay.
- Không cần hosted Waku Memory/MCP cho Niko chat memory.
- Không cần graph workflow engine trước khi memory baseline tốt lên.
- Không cần multi-user scoped memory ngay trong v1.
- Không nên để model tự sửa memory tự do khi chưa có guardrail.

Niko nên giữ Telegram gateway mỏng, ChatReplyGraph làm orchestration, decision
tasks nằm trong `bots/decision_model/`, memory pipeline nằm trong `niko/memory/`,
và dashboard chỉ quan sát/chỉnh sửa.
