from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid
from typing import Any

from niko.config import env_flag
from niko.memory.store import default_state_dir


MAX_TRACE_TEXT_LENGTH = 4000


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today_trace_name() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d.jsonl")


def truncate_trace_text(value: Any, limit: int = MAX_TRACE_TEXT_LENGTH) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


@dataclass(frozen=True)
class TraceTurn:
    turn_id: str
    conversation_id: str
    user_key: str
    started_at: str


class TraceLogger:
    def __init__(self, trace_dir: str | Path | None = None, enabled: bool | None = None) -> None:
        self.trace_dir = Path(trace_dir) if trace_dir is not None else default_state_dir() / "traces"
        self.enabled = env_flag("NIKO_TRACE_ENABLED", "1") if enabled is None else enabled
        self._lock = threading.RLock()
        if self.enabled:
            self.trace_dir.mkdir(parents=True, exist_ok=True)

    def turn_start(self, conversation_id: str, user_key: str, prompt: str, gateway_message=None) -> TraceTurn:
        turn = TraceTurn(
            turn_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            user_key=user_key,
            started_at=utc_now(),
        )
        self._write(
            {
                "type": "turn_start",
                "turn_id": turn.turn_id,
                "timestamp": turn.started_at,
                "conversation_id": conversation_id,
                "user_key": user_key,
                "prompt": truncate_trace_text(prompt),
                "gateway": self._gateway_meta(gateway_message),
            }
        )
        return turn

    def event(self, turn_id: str | None, kind: str, data: dict[str, Any] | None = None) -> None:
        self._write(
            {
                "type": "event",
                "turn_id": turn_id or "",
                "timestamp": utc_now(),
                "kind": kind,
                "data": data or {},
            }
        )

    def turn_end(
        self,
        turn_id: str | None,
        reply: str = "",
        status: str = "ok",
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "type": "turn_end",
            "turn_id": turn_id or "",
            "timestamp": utc_now(),
            "status": status,
            "reply": truncate_trace_text(reply),
            "data": data or {},
        }
        self._write(payload)

    def read_events(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.trace_dir.exists():
            return []

        events: list[dict[str, Any]] = []
        trace_files = sorted(self.trace_dir.glob("*.jsonl"), reverse=True)
        for path in trace_files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(events) >= limit:
                    return events
        return events

    def _write(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            path = self.trace_dir / today_trace_name()
            with path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def _gateway_meta(self, gateway_message) -> dict[str, Any]:
        if gateway_message is None:
            return {}
        return {
            "platform": gateway_message.platform,
            "chat_id": gateway_message.chat_id,
            "chat_type": gateway_message.chat_type,
            "chat_title": gateway_message.chat_title,
            "user_key": gateway_message.user.key,
            "user_label": gateway_message.user.label,
        }


_DEFAULT_TRACE_LOGGER: TraceLogger | None = None
_DEFAULT_TRACE_LOGGER_LOCK = threading.Lock()


def default_trace_logger() -> TraceLogger:
    global _DEFAULT_TRACE_LOGGER
    with _DEFAULT_TRACE_LOGGER_LOCK:
        trace_dir = default_state_dir() / "traces"
        enabled = env_flag("NIKO_TRACE_ENABLED", "1")
        if (
            _DEFAULT_TRACE_LOGGER is None
            or _DEFAULT_TRACE_LOGGER.trace_dir != trace_dir
            or _DEFAULT_TRACE_LOGGER.enabled != enabled
        ):
            _DEFAULT_TRACE_LOGGER = TraceLogger(trace_dir=trace_dir, enabled=enabled)
        return _DEFAULT_TRACE_LOGGER
