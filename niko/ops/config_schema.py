"""Schema cấu hình cho tab Config của Niko Ops dashboard.

File này là danh sách trắng những key runtime được phép chỉnh từ dashboard hoặc
`niko/.runtime/config.json`. Nó chỉ mô tả, chuẩn hóa, validate và mask giá trị
cấu hình; việc load env thực tế vẫn nằm ở `niko.config`.
"""

from __future__ import annotations

from typing import Any

from niko.config import env_value, env_value_with_source, read_runtime_config, runtime_config_path

MASKED_SECRET_VALUE = "********"

# CONFIG_SECTIONS là hợp đồng chung giữa backend và frontend:
# backend dùng để validate key, frontend dùng để render form theo từng tab con.
CONFIG_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "title": "Runtime",
        "description": "Chọn mode điều phối chính. Bật/tắt bot nằm ở tab Bots.",
        "fields": [
            {
                "name": "NIKO_AGENT_MODE",
                "label": "Agent mode",
                "type": "select",
                "default": "two_agent",
                "choices": ["two_agent", "single"],
            },
        ],
    },
    {
        "id": "telegram",
        "title": "Telegram Gateway",
        "description": "Token, allowlist và chính sách group cho bot Telegram.",
        "fields": [
            {"name": "TELEGRAM_BOT_TOKEN", "label": "Bot token", "type": "password", "default": "", "secret": True},
            {"name": "TELEGRAM_ALLOWED_CHAT_IDS", "label": "Allowed chat IDs", "type": "text", "default": ""},
            {"name": "CHAT_ALLOWED_USER_KEYS", "label": "Allowed user keys", "type": "text", "default": ""},
            {"name": "CHAT_USER_ALIASES", "label": "User aliases", "type": "textarea", "default": ""},
            {
                "name": "TELEGRAM_GROUP_MODE",
                "label": "Group mode",
                "type": "select",
                "default": "mentions",
                "choices": ["mentions", "all"],
            },
            {"name": "TELEGRAM_MENTION_REPLIES", "label": "Mention replies", "type": "bool", "default": "1"},
            {"name": "TELEGRAM_DROP_PENDING_UPDATES", "label": "Drop pending updates", "type": "bool", "default": "0"},
            {"name": "TELEGRAM_REQUEST_TIMEOUT_SECONDS", "label": "Request timeout", "type": "number", "default": "75"},
            {
                "name": "TELEGRAM_CHAT_ACTION_TIMEOUT_SECONDS",
                "label": "Typing timeout",
                "type": "number",
                "default": "5",
            },
            {"name": "TELEGRAM_STARTUP_RETRIES", "label": "Startup retries", "type": "number", "default": "2"},
            {
                "name": "TELEGRAM_STARTUP_RETRY_DELAY_SECONDS",
                "label": "Retry delay",
                "type": "number",
                "default": "3",
            },
        ],
    },
    {
        "id": "agents",
        "title": "Agent Commands",
        "description": "Command local cho Deep/Fast agent và sandbox CLI.",
        "fields": [
            {"name": "CLAUDE_CLI_COMMAND", "label": "Default CLI command", "type": "text", "default": "fcc-claude -p"},
            {
                "name": "CLAUDE_DEEP_AGENT_COMMAND",
                "label": "Deep agent command",
                "type": "text",
                "default": "",
            },
            {"name": "CLAUDE_WORKDIR", "label": "Claude workdir", "type": "text", "default": ""},
            {"name": "CLAUDE_TIMEOUT_SECONDS", "label": "Claude timeout", "type": "number", "default": "180"},
            {
                "name": "NIKO_FAST_AGENT_COMMAND",
                "label": "Fast agent command",
                "type": "text",
                "default": 'fcc-claude --model fable --bare --no-session-persistence --tools "" -p',
            },
            {"name": "NIKO_FAST_AGENT_TIMEOUT_SECONDS", "label": "Fast timeout", "type": "number", "default": "45"},
            {"name": "NIKO_PROMPT_HOOK_FILE", "label": "Prompt hook file", "type": "text", "default": "niko/HOOK.md"},
            {"name": "CHAT_IDENTITY_ENABLED", "label": "Inject chat identity", "type": "bool", "default": "1"},
        ],
    },
    {
        "id": "decision",
        "title": "Decision Model",
        "description": "Ollama/Nimble chỉ chọn label route, không sinh reply tự do.",
        "fields": [
            {
                "name": "NIKO_DECISION_MODEL_ENABLED",
                "label": "Enable Nimble triage",
                "type": "bool",
                "default": "1",
                "help": "Bật model local Ollama/Nimble cho các quyết định route nhanh. Tắt key này để dùng fallback/rule cũ khi Ollama không ổn định.",
            },
            {
                "name": "NIKO_DECISION_MODEL_BASE_URL",
                "label": "Ollama base URL",
                "type": "text",
                "default": "http://localhost:11434",
                "help": "Endpoint Ollama local mà decision model gọi tới. Thường giữ mặc định nếu Ollama chạy trên máy này.",
            },
            {
                "name": "NIKO_DECISION_MODEL_NAME",
                "label": "Model name",
                "type": "text",
                "default": "nimble",
                "help": "Tên model Ollama dùng cho route triage, sticker mood và memory gates.",
            },
            {
                "name": "NIKO_DECISION_MODEL_TIMEOUT_SECONDS",
                "label": "Timeout seconds",
                "type": "number",
                "default": "10",
                "help": "Thời gian tối đa cho một quyết định nhỏ. Tăng nếu GPU/model phản hồi chậm; giảm nếu muốn bot fallback nhanh.",
            },
            {
                "name": "NIKO_DECISION_MODEL_WARMUP_TIMEOUT_SECONDS",
                "label": "Warmup timeout",
                "type": "number",
                "default": "90",
                "help": "Thời gian dashboard chờ warmup model. Chỉ ảnh hưởng nút Warmup ở tab Bots.",
            },
            {
                "name": "NIKO_DECISION_MODEL_STOP_TIMEOUT_SECONDS",
                "label": "Stop timeout",
                "type": "number",
                "default": "15",
                "help": "Thời gian chờ unload/stop decision model khi bấm Stop trong dashboard.",
            },
            {
                "name": "NIKO_DECISION_MODEL_KEEP_ALIVE",
                "label": "Keep alive",
                "type": "text",
                "default": "-1",
                "help": "`-1` giữ model ở lại trong RAM/VRAM sau warmup. Đổi về thời lượng ngắn nếu cần nhường tài nguyên máy.",
            },
        ],
    },
    {
        "id": "sticker",
        "title": "Sticker",
        "description": "Sticker chạy nền sau reply; Nimble chọn mood rồi map sang sticker Duck.",
        "fields": [
            {"name": "TELEGRAM_STICKERS_ENABLED", "label": "Enable stickers", "type": "bool", "default": "1"},
            {
                "name": "TELEGRAM_STICKER_DECISION_MODEL_ENABLED",
                "label": "Enable sticker mood model",
                "type": "bool",
                "default": "1",
            },
            {
                "name": "TELEGRAM_STICKER_DECISION_MODEL_TIMEOUT_SECONDS",
                "label": "Sticker model timeout",
                "type": "number",
                "default": "5",
            },
            {
                "name": "TELEGRAM_STICKER_CONFIG_FILE",
                "label": "Sticker config file",
                "type": "text",
                "default": "bots/telegram/stickers/ducks.json",
            },
            {"name": "TELEGRAM_STICKER_SET_NAME", "label": "Sticker set", "type": "text", "default": "UtyaDuck"},
            {
                "name": "TELEGRAM_STICKER_MODE",
                "label": "Sticker mode",
                "type": "select",
                "default": "smart",
                "choices": ["smart", "always", "off"],
            },
            {"name": "TELEGRAM_STICKER_TIMEOUT_SECONDS", "label": "Telegram sticker timeout", "type": "number", "default": "5"},
        ],
    },
    {
        "id": "memory",
        "title": "Memory & Trace",
        "description": "Bật/tắt memory baseline, retrieval và JSONL tracing.",
        "fields": [
            {
                "name": "NIKO_TRACE_ENABLED",
                "label": "Enable traces",
                "type": "bool",
                "default": "1",
                "help": "Ghi trace JSONL cho từng turn để xem route, retrieval, write và lỗi. Nên bật khi debug memory.",
            },
            {
                "name": "NIKO_RUNTIME_LOG_ENABLED",
                "label": "Enable runtime logs",
                "type": "bool",
                "default": "1",
                "help": "Ghi runtime log cho bảng Bots/Runtime Log trên dashboard. Tắt nếu chỉ muốn chạy tối giản.",
            },
            {
                "name": "NIKO_MEMORY_ENABLED",
                "label": "Enable memory",
                "type": "bool",
                "default": "1",
                "help": "Công tắc tổng cho SQLite chat memory. Tắt key này sẽ bỏ qua cả log chat và long-term memory writes.",
            },
            {
                "name": "NIKO_MEMORY_RETRIEVAL_ENABLED",
                "label": "Enable retrieval",
                "type": "bool",
                "default": "1",
                "help": "Cho phép Deep agent đọc facts/episodes trước khi trả lời. Tắt để kiểm tra prompt không có memory context.",
            },
            {
                "name": "NIKO_MEMORY_GATE_ENABLED",
                "label": "Enable retrieval gate",
                "type": "bool",
                "default": "0",
                "help": "Dùng Nimble quyết định turn nào cần đọc memory trước khi gọi Deep agent. Default tắt để demo ổn định; bật khi muốn giảm retrieval thừa.",
            },
            {
                "name": "NIKO_MEMORY_WRITE_ENABLED",
                "label": "Enable memory writes",
                "type": "bool",
                "default": "1",
                "help": "Cho phép ghi chat_log và episodic memory. Write gate không chặn chat_log vận hành.",
            },
            {
                "name": "NIKO_MEMORY_WRITE_GATE_ENABLED",
                "label": "Enable write gate",
                "type": "bool",
                "default": "0",
                "help": "Dùng Nimble lọc xem Deep job có đáng ghi episode không. Bật sau khi runtime log đã dễ quan sát.",
            },
            {
                "name": "NIKO_MEMORY_CORRECTION_DETECTION_ENABLED",
                "label": "Memory correction detection",
                "default": "0",
                "type": "bool",
                "help": "Bật Nimble để nhận diện yêu cầu sửa hoặc xóa chat memory qua Telegram; Python vẫn kiểm soát update/delete.",
            },
            {
                "name": "NIKO_MEMORY_TOP_K",
                "label": "Memory top K",
                "type": "number",
                "default": "4",
                "help": "Số facts/episodes tối đa đưa vào Deep prompt mỗi lượt retrieval.",
            },
        ],
    },
    {
        "id": "replies",
        "title": "Replies",
        "description": "Các câu trả lời vận hành thường chỉnh khi demo.",
        "fields": [
            {"name": "NIKO_REPLY_SUFFIX", "label": "Reply suffix", "type": "text", "default": "Meow"},
            {"name": "NIKO_UNCERTAIN_DELAY_SECONDS", "label": "Uncertain delay", "type": "number", "default": "3"},
            {
                "name": "NIKO_DEEP_WAIT_REPLY",
                "label": "Deep wait reply",
                "type": "textarea",
                "default": "Dạ anh đợi em chút, câu này cần thêm thời gian xử lý.",
            },
            {
                "name": "NIKO_DEEP_BUSY_REPLY",
                "label": "Deep busy reply",
                "type": "textarea",
                "default": "Dạ anh đợi em chút, em vẫn đang xử lý câu trước.",
            },
            {
                "name": "NIKO_TOOL_UNAVAILABLE_REPLY",
                "label": "Tool unavailable reply",
                "type": "textarea",
                "default": "Dạ hiện tại em chưa có tool này trong harness.",
            },
        ],
    },
]


def config_field_index() -> dict[str, dict[str, Any]]:
    """Tạo index theo tên key để validate và mask giá trị không phải quét lại nhiều lần."""
    return {field["name"]: field for section in CONFIG_SECTIONS for field in section["fields"]}


def config_section_keys(section_id: str) -> list[str]:
    """Lấy danh sách key thuộc một section khi người dùng bấm reset cả nhóm."""
    for section in CONFIG_SECTIONS:
        if section["id"] == section_id:
            return [field["name"] for field in section["fields"]]
    return []


def normalize_config_value(field: dict[str, Any], value: Any) -> str:
    """Chuẩn hóa giá trị từ UI/JSON về string vì runtime config lưu dạng env-like."""
    field_type = field.get("type", "text")
    if field_type == "bool":
        if isinstance(value, bool):
            return "1" if value else "0"
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return "1"
        if normalized in {"0", "false", "no", "off"}:
            return "0"
        raise ValueError(f"{field['name']} phải là boolean.")
    if field_type == "number":
        raw = str(value).strip()
        try:
            number = float(raw)
        except ValueError as exc:
            raise ValueError(f"{field['name']} phải là số.") from exc
        if number.is_integer():
            return str(int(number))
        return str(number)
    if field_type == "select":
        raw = str(value).strip()
        if raw not in field.get("choices", []):
            raise ValueError(f"{field['name']} không nằm trong lựa chọn hợp lệ.")
        return raw
    return str(value)


def positive_float_config(name: str, default: float, *, minimum: float = 0.1) -> float:
    """Đọc timeout dạng float cho các action dashboard và chặn giá trị quá nhỏ."""
    raw = env_value(name, str(default)).strip()
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def display_config_value(field: dict[str, Any], value: str) -> str:
    """Ẩn secret trước khi trả snapshot về UI hoặc runtime log."""
    if field.get("secret"):
        return MASKED_SECRET_VALUE if value else ""
    return value


def validate_config_updates(values: dict[str, Any]) -> dict[str, str]:
    """Validate payload từ dashboard trước khi ghi vào runtime config.

    Chỉ key có trong CONFIG_SECTIONS mới được ghi. Nếu một key đang đến từ OS env
    thì dashboard coi như bị khóa để tránh ghi file config nhưng runtime lại không
    bao giờ dùng giá trị đó.
    """
    fields = config_field_index()
    updates: dict[str, str] = {}
    for name, value in values.items():
        if name not in fields:
            raise ValueError(f"Không cho phép cấu hình key: {name}")
        _, source, _ = env_value_with_source(name, fields[name].get("default", ""))
        if source == "os":
            raise ValueError(f"{name} đang bị khóa bởi OS env.")
        if fields[name].get("secret") and str(value) == MASKED_SECRET_VALUE:
            # UI gửi lại dấu mask khi người dùng không sửa secret; bỏ qua để không ghi "********".
            continue
        updates[name] = normalize_config_value(fields[name], value)
    return updates


def masked_runtime_overrides(overrides: dict[str, str]) -> dict[str, str]:
    """Trả runtime overrides đã mask secret để snapshot không lộ token local."""
    fields = config_field_index()
    masked: dict[str, str] = {}
    for key, value in overrides.items():
        field = fields.get(key, {})
        masked[key] = display_config_value(field, value)
    return masked


def config_snapshot(bot_manager: "TelegramBotProcessManager | None" = None) -> dict[str, Any]:
    """Gom schema, giá trị hiệu lực và nguồn giá trị cho tab Config.

    Frontend cần cả `value` đang hiệu lực lẫn `runtime_value` đang nằm trong
    config.json để phân biệt default, runtime override và OS env.
    """
    overrides = read_runtime_config()
    sections: list[dict[str, Any]] = []
    for section in CONFIG_SECTIONS:
        fields = []
        for field in section["fields"]:
            value, source, effective_key = env_value_with_source(field["name"], field.get("default", ""))
            raw_runtime_value = overrides.get(field["name"])
            fields.append(
                {
                    **field,
                    "value": display_config_value(field, value),
                    "source": source,
                    "effective_key": effective_key,
                    "editable": source != "os",
                    "runtime_value": None
                    if raw_runtime_value is None
                    else display_config_value(field, raw_runtime_value),
                    "is_set": bool(value),
                }
            )
        sections.append({**section, "fields": fields})
    return {
        "path": str(runtime_config_path()),
        "overrides": masked_runtime_overrides(overrides),
        "sections": sections,
        "bot": bot_manager.status() if bot_manager is not None else {},
    }

