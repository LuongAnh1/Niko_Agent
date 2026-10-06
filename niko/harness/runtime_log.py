"""Structured runtime log cho dashboard Ops.

Terminal chỉ nên còn thông báo bootstrap tối thiểu. Các mốc vận hành của bot,
decision model, sticker và graph được ghi JSONL ở đây để dashboard đọc thành
bảng log ổn định, có thể lọc theo source/level/event.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

from niko.config import env_flag
from niko.harness.trace import today_trace_name, truncate_trace_text, utc_now
from niko.memory.store import default_state_dir


MAX_RUNTIME_LOG_DATA_LENGTH = 1200
SENSITIVE_KEY_PARTS = ("authorization", "password", "secret", "token")


def _safe_value(key: str, value: Any) -> Any:
    lowered_key = key.lower()
    if any(part in lowered_key for part in SENSITIVE_KEY_PARTS):
        return "***" if value else ""
    if isinstance(value, dict):
        return {str(child_key): _safe_value(str(child_key), child_value) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_safe_value(key, item) for item in value[:50]]
    if isinstance(value, tuple):
        return [_safe_value(key, item) for item in value[:50]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return truncate_trace_text(value, MAX_RUNTIME_LOG_DATA_LENGTH)
        return value
    return truncate_trace_text(str(value), MAX_RUNTIME_LOG_DATA_LENGTH)


def sanitize_log_data(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    return {str(key): _safe_value(str(key), value) for key, value in data.items()}


class RuntimeEventLogger:
    """Append-only JSONL logger cho log vận hành hiển thị trong tab Bots."""

    def __init__(self, log_dir: str | Path | None = None, enabled: bool | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else default_state_dir() / "logs"
        self.enabled = env_flag("NIKO_RUNTIME_LOG_ENABLED", "1") if enabled is None else enabled
        self._lock = threading.RLock()
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def event(
        self,
        source: str,
        event: str,
        message: str = "",
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Ghi một event ngắn, không bao giờ để lỗi log làm gãy luồng bot."""
        if not self.enabled:
            return
        payload = {
            "timestamp": utc_now(),
            "source": str(source or "runtime"),
            "level": str(level or "info"),
            "event": str(event or "event"),
            "message": truncate_trace_text(message, MAX_RUNTIME_LOG_DATA_LENGTH),
            "data": sanitize_log_data(data),
        }
        try:
            self._write(payload)
        except Exception:
            pass

    def read_events(self, limit: int = 200, source: str = "") -> list[dict[str, Any]]:
        """Đọc ngược từ log mới nhất để dashboard lấy bảng gần đây."""
        if not self.log_dir.exists():
            return []

        normalized_source = source.strip()
        events: list[dict[str, Any]] = []
        log_files = sorted(self.log_dir.glob("*.jsonl"), reverse=True)
        for path in log_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if normalized_source and event.get("source") != normalized_source:
                    continue
                events.append(event)
                if len(events) >= limit:
                    return events
        return events

    def _write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / today_trace_name()
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


_DEFAULT_RUNTIME_LOGGER: RuntimeEventLogger | None = None
_DEFAULT_RUNTIME_LOGGER_LOCK = threading.Lock()


def default_runtime_logger() -> RuntimeEventLogger:
    """Singleton theo state dir hiện tại; đổi config thì logger tự mở path mới."""
    global _DEFAULT_RUNTIME_LOGGER
    with _DEFAULT_RUNTIME_LOGGER_LOCK:
        log_dir = default_state_dir() / "logs"
        enabled = env_flag("NIKO_RUNTIME_LOG_ENABLED", "1")
        if (
            _DEFAULT_RUNTIME_LOGGER is None
            or _DEFAULT_RUNTIME_LOGGER.log_dir != log_dir
            or _DEFAULT_RUNTIME_LOGGER.enabled != enabled
        ):
            _DEFAULT_RUNTIME_LOGGER = RuntimeEventLogger(log_dir=log_dir, enabled=enabled)
        return _DEFAULT_RUNTIME_LOGGER


__all__ = ["RuntimeEventLogger", "default_runtime_logger", "sanitize_log_data"]
