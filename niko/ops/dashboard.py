"""HTTP entrypoint mỏng cho Niko Ops dashboard.

File này chỉ ghép các API quan sát/vận hành lại với nhau: trace, memory,
runtime log, config và bot controls. Logic riêng của từng mảng nằm ở module
chuyên trách để dashboard không trở thành nơi chứa policy của agent.

Các endpoint consolidation trong dashboard là thao tác vận hành thủ công:
Refresh batch chỉ preview read-only, Run once mới xử lý một batch. Scheduler tự
động theo N tin nhắn chưa nằm trong dashboard entrypoint hiện tại.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from niko.config import env_value, load_env_files, reset_runtime_config, update_runtime_config
from niko.harness.runtime_log import RuntimeEventLogger, default_runtime_logger
from niko.harness.trace import TraceLogger, default_trace_logger
from niko.memory.consolidation import MemoryConsolidator
from niko.memory.store import MemoryStore, default_memory_store
from niko.ops.bots import TelegramBotProcessManager, bots_snapshot, run_bot_action
from niko.ops.config_schema import config_section_keys, config_snapshot, validate_config_updates
from niko.ops.frontend import dashboard_html


DEFAULT_OPS_HOST = "127.0.0.1"
DEFAULT_OPS_PORT = 7777


def json_bytes(payload: dict[str, Any], status: int = 200) -> tuple[int, bytes, str]:
    """Đóng gói response JSON theo cùng một format cho mọi endpoint."""
    return status, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), "application/json"


def html_bytes(text: str) -> tuple[int, bytes, str]:
    """Trả HTML đã render sẵn cho trình duyệt."""
    return 200, text.encode("utf-8"), "text/html; charset=utf-8"


def make_handler(
    memory_store: MemoryStore | None = None,
    trace_logger: TraceLogger | None = None,
    bot_manager: TelegramBotProcessManager | None = None,
    runtime_logger: RuntimeEventLogger | None = None,
):
    """Tạo HTTP handler cho Ops dashboard với dependency injection cho test.

    Dashboard chạy bằng stdlib HTTP server nên handler được dựng trong hàm này
    để test có thể truyền memory store, trace logger và bot manager giả lập.
    """
    store = memory_store or default_memory_store()
    consolidator = MemoryConsolidator(store=store)
    traces = trace_logger or default_trace_logger()
    runtime_logs = runtime_logger or default_runtime_logger()
    manager = bot_manager or TelegramBotProcessManager(runtime_logger=runtime_logs)

    class NikoOpsHandler(BaseHTTPRequestHandler):
        server_version = "NikoOps/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(*html_bytes(dashboard_html()))
                return
            if parsed.path == "/api/snapshot":
                # Snapshot tổng hợp cho lần render đầu và các lần refresh định kỳ của UI.
                consolidation_batch = consolidator.preview_next_batch()
                self._send(
                    *json_bytes(
                        {
                            "memory": store.snapshot(),
                            "consolidation": {
                                "batch": consolidation_batch.to_dict(),
                                "candidates": [
                                    candidate.to_dict()
                                    for candidate in consolidator.build_candidates(consolidation_batch)
                                ],
                            },
                            "traces": traces.read_events(limit=80),
                            "config": config_snapshot(manager),
                            "bots": bots_snapshot(manager),
                            "logs": runtime_logs.read_events(limit=160),
                        }
                    )
                )
                return
            if parsed.path == "/api/config":
                self._send(*json_bytes({"config": config_snapshot(manager)}))
                return
            if parsed.path == "/api/bots":
                self._send(
                    *json_bytes(
                        {
                            "bots": bots_snapshot(manager),
                            "logs": runtime_logs.read_events(limit=self._query_limit(parsed.query, default=160)),
                        }
                    )
                )
                return
            if parsed.path == "/api/runtime/logs":
                query = parse_qs(parsed.query)
                source = (query.get("source") or [""])[0]
                # Cho phép bảng Bots lọc log theo source mà không phải tải toàn bộ rồi tự lọc.
                self._send(
                    *json_bytes(
                        {
                            "logs": runtime_logs.read_events(
                                limit=self._query_limit(parsed.query, default=200),
                                source=source,
                            )
                        }
                    )
                )
                return
            if parsed.path == "/api/runtime/bot":
                self._send(*json_bytes({"bot": manager.status()}))
                return
            if parsed.path == "/api/traces":
                limit = self._query_limit(parsed.query)
                self._send(*json_bytes({"traces": traces.read_events(limit=limit)}))
                return
            if parsed.path == "/api/memory":
                consolidation_batch = consolidator.preview_next_batch()
                self._send(
                    *json_bytes(
                        {
                            "memory": store.snapshot(),
                            "consolidation": {
                                "batch": consolidation_batch.to_dict(),
                                "candidates": [
                                    candidate.to_dict()
                                    for candidate in consolidator.build_candidates(consolidation_batch)
                                ],
                            },
                        }
                    )
                )
                return
            if parsed.path == "/api/memory/consolidation":
                # Refresh batch: chỉ xem batch/candidate kế tiếp, không ghi memory và không mark row.
                batch = consolidator.preview_next_batch(limit=self._query_limit(parsed.query, default=12))
                self._send(
                    *json_bytes(
                        {
                            "batch": batch.to_dict(),
                            "candidates": [candidate.to_dict() for candidate in consolidator.build_candidates(batch)],
                        }
                    )
                )
                return
            self._send(*json_bytes({"error": "not found"}, status=404))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/memory/facts":
                data = self._read_json()
                try:
                    # Ops chỉ thêm fact thủ công; extraction tự động chưa thuộc baseline này.
                    fact_id = store.add_fact(
                        str(data.get("subject", "")),
                        str(data.get("content", "")),
                        source=str(data.get("source", "ops") or "ops"),
                        meta=data.get("meta") if isinstance(data.get("meta"), dict) else None,
                    )
                except ValueError as exc:
                    self._send(*json_bytes({"error": str(exc)}, status=400))
                    return
                self._send(*json_bytes({"id": fact_id}, status=201))
                return
            if parsed.path == "/api/memory/consolidation/run":
                data = self._read_json()
                raw_limit = data.get("limit")
                try:
                    limit = None if raw_limit in (None, "") else max(1, min(100, int(raw_limit)))
                except (TypeError, ValueError):
                    self._send(*json_bytes({"error": "invalid consolidation limit"}, status=400))
                    return
                session_id = str(data.get("session_id", "")).strip() or None
                # Run once: trigger thủ công đúng một batch; không có loop/scheduler nền ở endpoint này.
                result = consolidator.run_once(limit=limit, session_id=session_id)
                status = 500 if result.status == "error" else 200
                self._send(*json_bytes({"result": result.to_dict()}, status=status))
                return
            if parsed.path == "/api/config":
                data = self._read_json()
                values = data.get("values") if isinstance(data.get("values"), dict) else data
                try:
                    # Schema config chịu trách nhiệm validate và chặn ghi đè OS env.
                    update_runtime_config(validate_config_updates(values))
                except ValueError as exc:
                    self._send(*json_bytes({"error": str(exc)}, status=400))
                    return
                self._send(*json_bytes({"config": config_snapshot(manager)}))
                return
            if parsed.path == "/api/config/reset":
                data = self._read_json()
                keys: list[str] = []
                if isinstance(data.get("keys"), list):
                    keys.extend(str(key) for key in data["keys"])
                if data.get("section"):
                    # Reset theo section để UI không phải hard-code danh sách key ở frontend.
                    keys.extend(config_section_keys(str(data["section"])))
                if not keys:
                    self._send(*json_bytes({"error": "missing keys or section"}, status=400))
                    return
                reset_runtime_config(keys)
                self._send(*json_bytes({"config": config_snapshot(manager)}))
                return
            if parsed.path == "/api/runtime/bot/start":
                self._send(*json_bytes({"bot": manager.start()}))
                return
            if parsed.path == "/api/runtime/bot/stop":
                self._send(*json_bytes({"bot": manager.stop()}))
                return
            if parsed.path.startswith("/api/bots/"):
                parts = [part for part in parsed.path.split("/") if part]
                if len(parts) == 4:
                    _, _, bot_id, action = parts
                    try:
                        bot = run_bot_action(manager, bot_id, action)
                    except ValueError as exc:
                        self._send(*json_bytes({"error": str(exc)}, status=400))
                        return
                    self._send(*json_bytes({"bot": bot, "bots": bots_snapshot(manager)}))
                    return
            self._send(*json_bytes({"error": "not found"}, status=404))

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            prefix = "/api/memory/facts/"
            if parsed.path.startswith(prefix):
                # Delete chỉ áp dụng cho fact thủ công, không đụng chat_log hay episodes.
                raw_id = parsed.path[len(prefix) :]
                try:
                    fact_id = int(raw_id)
                except ValueError:
                    self._send(*json_bytes({"error": "invalid fact id"}, status=400))
                    return
                deleted = store.delete_fact(fact_id)
                self._send(*json_bytes({"deleted": deleted}, status=200 if deleted else 404))
                return
            self._send(*json_bytes({"error": "not found"}, status=404))

        def log_message(self, format: str, *args: Any) -> None:
            """Tắt access log mặc định để terminal chỉ còn runtime log có cấu trúc."""
            return

        def _query_limit(self, query: str, default: int = 200) -> int:
            """Giới hạn số bản ghi để UI không vô tình kéo quá nhiều trace/log."""
            params = parse_qs(query)
            try:
                return max(1, min(1000, int(params.get("limit", [str(default)])[0])))
            except ValueError:
                return default

        def _read_json(self) -> dict[str, Any]:
            """Đọc body JSON nhỏ từ dashboard và bỏ qua payload không phải object."""
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            """Ghi response HTTP thấp cấp vì dashboard cố ý không kéo framework web."""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return NikoOpsHandler


def create_server(
    host: str = DEFAULT_OPS_HOST,
    port: int = DEFAULT_OPS_PORT,
    memory_store: MemoryStore | None = None,
    trace_logger: TraceLogger | None = None,
    bot_manager: TelegramBotProcessManager | None = None,
    runtime_logger: RuntimeEventLogger | None = None,
) -> ThreadingHTTPServer:
    """Dựng server đa luồng để request API không chặn refresh giao diện."""
    return ThreadingHTTPServer((host, port), make_handler(memory_store, trace_logger, bot_manager, runtime_logger))


def main() -> int:
    """Entrypoint chạy dashboard local từ `python -m niko.ops.dashboard`."""
    load_env_files("telegram")
    host = env_value("NIKO_OPS_HOST", DEFAULT_OPS_HOST)
    raw_port = env_value("NIKO_OPS_PORT", str(DEFAULT_OPS_PORT))
    try:
        port = int(raw_port)
    except ValueError:
        port = DEFAULT_OPS_PORT

    server = create_server(host, port)
    print(f"Niko Ops dang chay tai http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDa dung Niko Ops.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
