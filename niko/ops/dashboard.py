from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlparse
from typing import Any

from niko.config import (
    env_value,
    env_value_with_source,
    load_env_files,
    read_runtime_config,
    repo_root,
    reset_runtime_config,
    runtime_config_path,
    update_runtime_config,
)
from niko.harness.trace import TraceLogger, default_trace_logger
from niko.memory.store import MemoryStore, default_memory_store


DEFAULT_OPS_HOST = "127.0.0.1"
DEFAULT_OPS_PORT = 7777

CONFIG_SECTIONS: list[dict[str, Any]] = [
    {
        "id": "runtime",
        "title": "Runtime",
        "description": "Bật/tắt bot Telegram và chọn mode điều phối chính.",
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
        "id": "decision",
        "title": "Decision Model",
        "description": "Ollama/Nimble chỉ chọn label route, không sinh reply tự do.",
        "fields": [
            {"name": "NIKO_DECISION_MODEL_ENABLED", "label": "Enable Nimble triage", "type": "bool", "default": "1"},
            {
                "name": "NIKO_DECISION_MODEL_BASE_URL",
                "label": "Ollama base URL",
                "type": "text",
                "default": "http://localhost:11434",
            },
            {"name": "NIKO_DECISION_MODEL_NAME", "label": "Model name", "type": "text", "default": "nimble"},
            {
                "name": "NIKO_DECISION_MODEL_TIMEOUT_SECONDS",
                "label": "Timeout seconds",
                "type": "number",
                "default": "10",
            },
            {"name": "NIKO_DECISION_MODEL_KEEP_ALIVE", "label": "Keep alive", "type": "text", "default": "-1"},
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
            {"name": "NIKO_TRACE_ENABLED", "label": "Enable traces", "type": "bool", "default": "1"},
            {"name": "NIKO_MEMORY_ENABLED", "label": "Enable memory", "type": "bool", "default": "1"},
            {"name": "NIKO_MEMORY_RETRIEVAL_ENABLED", "label": "Enable retrieval", "type": "bool", "default": "1"},
            {"name": "NIKO_MEMORY_WRITE_ENABLED", "label": "Enable memory writes", "type": "bool", "default": "1"},
            {"name": "NIKO_MEMORY_TOP_K", "label": "Memory top K", "type": "number", "default": "4"},
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
    return {field["name"]: field for section in CONFIG_SECTIONS for field in section["fields"]}


def config_section_keys(section_id: str) -> list[str]:
    for section in CONFIG_SECTIONS:
        if section["id"] == section_id:
            return [field["name"] for field in section["fields"]]
    return []


def normalize_config_value(field: dict[str, Any], value: Any) -> str:
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


def validate_config_updates(values: dict[str, Any]) -> dict[str, str]:
    fields = config_field_index()
    updates: dict[str, str] = {}
    for name, value in values.items():
        if name not in fields:
            raise ValueError(f"Không cho phép cấu hình key: {name}")
        _, source, _ = env_value_with_source(name, fields[name].get("default", ""))
        if source == "os":
            raise ValueError(f"{name} đang bị khóa bởi OS env.")
        updates[name] = normalize_config_value(fields[name], value)
    return updates


def config_snapshot(bot_manager: "TelegramBotProcessManager | None" = None) -> dict[str, Any]:
    overrides = read_runtime_config()
    sections: list[dict[str, Any]] = []
    for section in CONFIG_SECTIONS:
        fields = []
        for field in section["fields"]:
            value, source, effective_key = env_value_with_source(field["name"], field.get("default", ""))
            fields.append(
                {
                    **field,
                    "value": value,
                    "source": source,
                    "effective_key": effective_key,
                    "editable": source != "os",
                    "runtime_value": overrides.get(field["name"]),
                }
            )
        sections.append({**section, "fields": fields})
    return {
        "path": str(runtime_config_path()),
        "overrides": overrides,
        "sections": sections,
        "bot": bot_manager.status() if bot_manager is not None else {},
    }


class TelegramBotProcessManager:
    """Quản lý bot Telegram do dashboard start; không đụng process chạy ngoài."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None:
                return {"managed": False, "running": False, "pid": None, "returncode": None}
            return {
                "managed": True,
                "running": process.poll() is None,
                "pid": process.pid,
                "returncode": process.poll(),
            }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.status()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                [sys.executable, "-m", "bots.telegram.bot"],
                cwd=str(repo_root()),
                env=os.environ.copy(),
                creationflags=creationflags,
            )
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return self.status()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            return self.status()


INDEX_HTML = """<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Niko Ops</title>
  <!-- Design language adapted from Waku Ops (MIT), trimmed for Niko's local stdlib dashboard. -->
  <style>
    :root {
      color-scheme: light;
      --surface-bg: #e1e1d9;
      --surface-paper: #d8d8cf;
      --surface-raised: #cecec4;
      --surface-sunk: #c5c5ba;
      --text-ink: #161614;
      --text-muted: #34342f;
      --text-faint: #66665d;
      --rule: rgba(22, 22, 20, .18);
      --rule-hard: rgba(22, 22, 20, .38);
      --accent: #e1ae05;
      --accent-fg: #735903;
      --ok: #2b7754;
      --warn: #8c5617;
      --bad: #aa2e11;
      --radius: 7px;
      --space-1: 4px;
      --space-2: 8px;
      --space-3: 12px;
      --space-4: 16px;
      --space-5: 20px;
      --space-6: 24px;
      --mono: "Cascadia Mono", "SFMono-Regular", Consolas, monospace;
      --sans: Inter, "Segoe UI", Arial, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      height: 100vh;
      overflow: hidden;
      display: flex;
      background: var(--surface-bg);
      color: var(--text-ink);
      font: 14px/1.5 var(--sans);
    }
    button, input, textarea { font: inherit; }
    button {
      border: 1px solid var(--rule-hard);
      border-radius: var(--radius);
      background: var(--surface-paper);
      color: var(--text-ink);
      padding: 7px 10px;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: var(--text-ink);
      font-weight: 600;
    }
    button.danger { color: var(--bad); }
    input, textarea {
      width: 100%;
      border: 1px solid var(--rule-hard);
      border-radius: var(--radius);
      background: var(--surface-bg);
      color: var(--text-ink);
      padding: 9px 10px;
      outline: none;
    }
    input:focus, textarea:focus, button:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    textarea { min-height: 92px; resize: vertical; }
    .rail {
      width: 204px;
      flex: 0 0 204px;
      height: 100vh;
      overflow-y: auto;
      background: var(--surface-paper);
      border-right: 1px solid var(--rule);
      display: flex;
      flex-direction: column;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: var(--space-3);
      padding: var(--space-5) var(--space-4);
      border-bottom: 1px solid var(--rule);
    }
    .mark {
      width: 28px;
      height: 28px;
      display: grid;
      place-items: center;
      border: 1px solid var(--rule-hard);
      border-radius: 50%;
      background: var(--surface-bg);
      font: 700 13px/1 var(--mono);
    }
    .brand strong {
      display: block;
      font: 700 13px/1.2 var(--mono);
      letter-spacing: .08em;
    }
    .brand span {
      color: var(--text-faint);
      font: 12px/1.2 var(--mono);
    }
    .nav-group {
      padding: var(--space-5) var(--space-4) var(--space-2);
      color: var(--text-faint);
      font: 700 11px/1 var(--mono);
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .rail a {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      margin: 0 var(--space-2);
      padding: 8px var(--space-3) 8px var(--space-5);
      border-left: 2px solid transparent;
      color: var(--text-muted);
      font: 13px/1.3 var(--mono);
      text-decoration: none;
    }
    .rail a:hover, .rail a.on {
      color: var(--text-ink);
      background: var(--surface-raised);
    }
    .rail a.on { border-left-color: var(--accent); }
    .nav-count {
      min-width: 22px;
      text-align: right;
      color: var(--text-faint);
      font-variant-numeric: tabular-nums;
    }
    .rail-foot {
      margin-top: auto;
      padding: var(--space-4);
      border-top: 1px solid var(--rule);
      color: var(--text-faint);
      font: 12px/1.4 var(--mono);
    }
    main {
      flex: 1;
      min-width: 0;
      height: 100vh;
      overflow-y: auto;
      padding: 0 var(--space-6) var(--space-6);
    }
    .pagehead {
      position: sticky;
      top: 0;
      z-index: 5;
      background: var(--surface-bg);
      border-bottom: 1px solid var(--rule);
      padding: var(--space-6) 0 var(--space-4);
      margin-bottom: var(--space-5);
    }
    .headrow { display: flex; align-items: flex-end; gap: var(--space-4); }
    h1 {
      margin: 0;
      font-size: 25px;
      line-height: 1.1;
      font-weight: 650;
      letter-spacing: 0;
    }
    .sub {
      margin-top: 4px;
      color: var(--text-faint);
      font: 12px/1.4 var(--mono);
    }
    .actions { margin-left: auto; display: flex; gap: var(--space-2); }
    .tiles {
      display: grid;
      grid-template-columns: repeat(4, minmax(132px, 1fr));
      gap: var(--space-3);
      margin-bottom: var(--space-5);
    }
    .tile, .panel, .record {
      background: var(--surface-paper);
      border: 1px solid var(--rule);
      border-radius: var(--radius);
    }
    .tile { padding: var(--space-4); }
    .tile .k {
      color: var(--text-faint);
      font: 700 11px/1 var(--mono);
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .tile .v {
      margin-top: var(--space-2);
      font-size: 29px;
      line-height: 1;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr);
      gap: var(--space-4);
      align-items: start;
    }
    .panel { padding: var(--space-4); margin-bottom: var(--space-4); }
    .panel h2 {
      margin: 0 0 var(--space-3);
      color: var(--text-muted);
      font: 700 11px/1 var(--mono);
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .form-grid { display: grid; gap: var(--space-3); }
    .records { display: grid; gap: var(--space-3); }
    .record { padding: var(--space-4); }
    .record-head {
      display: flex;
      align-items: baseline;
      gap: var(--space-2);
      margin-bottom: var(--space-2);
    }
    .record-title { font-weight: 650; }
    .record-actions { margin-left: auto; display: flex; gap: var(--space-2); }
    .meta {
      color: var(--text-faint);
      font: 12px/1.4 var(--mono);
      overflow-wrap: anywhere;
    }
    .bodytext { white-space: pre-wrap; overflow-wrap: anywhere; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 3px 8px;
      border: 1px solid var(--rule-hard);
      border-radius: 999px;
      color: var(--text-muted);
      font: 700 11px/1 var(--mono);
      letter-spacing: .08em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .badge.ok { color: var(--ok); border-color: currentColor; }
    .badge.warn { color: var(--warn); border-color: currentColor; }
    .badge.bad { color: var(--bad); border-color: currentColor; }
    .map {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: var(--space-2);
    }
    .step {
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      background: var(--surface-bg);
      padding: var(--space-3);
      min-height: 86px;
    }
    .step b { display: block; margin-bottom: 6px; }
    .step span { color: var(--text-faint); font-size: 12px; }
    .graph-card {
      overflow-x: auto;
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      background: var(--surface-bg);
      padding: var(--space-3);
    }
    .run-graph {
      width: 100%;
      min-width: 1040px;
      height: auto;
      display: block;
      font-family: var(--sans);
    }
    .run-graph .zone {
      fill: rgba(225, 174, 5, .05);
      stroke: var(--rule);
      stroke-width: 1.2;
      stroke-dasharray: 7 6;
    }
    .run-graph .zone.done { stroke: var(--ok); }
    .run-graph .zone.hot {
      stroke: var(--accent);
      stroke-width: 2;
      filter: drop-shadow(0 0 6px rgba(225, 174, 5, .28));
    }
    .run-graph .zone.bad {
      stroke: var(--bad);
      stroke-width: 2;
    }
    .run-graph .zone-label {
      fill: var(--text-faint);
      font: 700 11px/1 var(--mono);
      letter-spacing: .1em;
      text-transform: uppercase;
    }
    .run-graph .node rect,
    .run-graph .gate-node path {
      fill: var(--surface-paper);
      stroke: var(--rule-hard);
      stroke-width: 1.2;
    }
    .run-graph .node text { fill: var(--text-ink); font-size: 13px; font-weight: 650; }
    .run-graph .node .subt { fill: var(--text-faint); font-size: 11px; font-weight: 400; }
    .run-graph .node.future rect {
      fill: var(--surface-bg);
      stroke-dasharray: 6 5;
    }
    .run-graph .node.done rect,
    .run-graph .gate-node.done path {
      stroke: var(--ok);
      stroke-width: 2;
      fill: var(--surface-raised);
    }
    .run-graph .node.hot rect,
    .run-graph .gate-node.hot path {
      stroke: var(--accent);
      stroke-width: 3;
      fill: #ded2a4;
      filter: drop-shadow(0 0 7px rgba(225, 174, 5, .42));
    }
    .run-graph .node.bad rect,
    .run-graph .gate-node.bad path {
      stroke: var(--bad);
      stroke-width: 2.4;
    }
    .run-graph .gate-node text { fill: var(--text-ink); font-size: 13px; font-weight: 650; }
    .run-graph .gate-node .subt { fill: var(--text-faint); font-size: 11px; font-weight: 400; }
    .run-flow {
      fill: none;
      stroke: var(--text-faint);
      stroke-width: 1.4;
      opacity: .58;
      marker-end: url(#arrow);
    }
    .run-flow.done {
      stroke: var(--ok);
      opacity: .88;
      stroke-width: 2;
    }
    .run-flow.live {
      stroke: var(--accent);
      opacity: 1;
      stroke-width: 3;
      stroke-dasharray: 7 6;
      animation: flowdash .55s linear infinite;
    }
    .run-flow.dash {
      stroke-dasharray: 5 5;
      marker-end: none;
    }
    @keyframes flowdash { to { stroke-dashoffset: -26; } }
    .graph-status {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
      margin-bottom: var(--space-3);
    }
    .live-dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--accent);
      margin-right: 6px;
      animation: pulse 1s ease-in-out infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
    @media (prefers-reduced-motion: reduce) {
      .run-flow.live, .live-dot { animation: none; }
    }
    .trace {
      font: 12px/1.45 var(--mono);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      max-height: 280px;
      overflow: auto;
      background: var(--surface-bg);
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      padding: var(--space-3);
    }
    .empty {
      border: 1px dashed var(--rule-hard);
      border-radius: var(--radius);
      padding: var(--space-4);
      color: var(--text-faint);
      background: var(--surface-bg);
    }
    .splitline {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--space-3);
    }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: var(--space-2);
      margin-bottom: var(--space-4);
    }
    .tabs button.on {
      border-color: var(--accent);
      background: var(--surface-raised);
      font-weight: 650;
    }
    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(260px, 1fr));
      gap: var(--space-3);
    }
    .field {
      display: grid;
      gap: 6px;
      padding: var(--space-3);
      border: 1px solid var(--rule);
      border-radius: var(--radius);
      background: var(--surface-bg);
    }
    .field label {
      display: flex;
      justify-content: space-between;
      gap: var(--space-2);
      color: var(--text-muted);
      font: 700 11px/1.2 var(--mono);
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .field select {
      width: 100%;
      border: 1px solid var(--rule-hard);
      border-radius: var(--radius);
      background: var(--surface-bg);
      color: var(--text-ink);
      padding: 9px 10px;
    }
    .checkline {
      display: flex;
      align-items: center;
      gap: var(--space-2);
      min-height: 38px;
    }
    .checkline input { width: auto; }
    .source {
      color: var(--text-faint);
      font: 11px/1.3 var(--mono);
    }
    @media (max-width: 1100px) {
      body { overflow: auto; height: auto; display: block; }
      .rail { width: 100%; height: auto; flex: none; border-right: 0; border-bottom: 1px solid var(--rule); }
      .rail a { display: inline-flex; min-width: 132px; }
      .nav-group, .rail-foot { display: none; }
      main { height: auto; overflow: visible; padding: 0 var(--space-4) var(--space-5); }
      .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid, .map { grid-template-columns: 1fr; }
    }
    @media (max-width: 620px) {
      .tiles { grid-template-columns: 1fr; }
      .headrow { align-items: flex-start; flex-direction: column; }
      .actions { margin-left: 0; }
    }
  </style>
</head>
<body>
  <nav class="rail" aria-label="Niko Ops navigation">
    <div class="brand">
      <div class="mark">N</div>
      <div>
        <strong>NIKO OPS</strong>
        <span>local harness</span>
      </div>
    </div>
    <div class="nav-group">System</div>
    <a href="#overview" data-view="overview">Overview <span class="nav-count" id="n-overview"></span></a>
    <a href="#memory" data-view="memory">Memory <span class="nav-count" id="n-memory"></span></a>
    <a href="#chat" data-view="chat">Chat <span class="nav-count" id="n-chat"></span></a>
    <a href="#traces" data-view="traces">Traces <span class="nav-count" id="n-traces"></span></a>
    <a href="#config" data-view="config">Config <span class="nav-count" id="n-config"></span></a>
    <div class="nav-group">Runtime</div>
    <a href="#ops" data-view="ops">Ops <span class="nav-count" id="n-ops"></span></a>
    <div class="rail-foot">
      SQLite memory<br>
      JSONL traces<br>
      stdlib dashboard
    </div>
  </nav>
  <main>
    <header class="pagehead">
      <div class="headrow">
        <div>
          <h1 id="title">Overview</h1>
          <div class="sub" id="subtitle">Harness health, memory state, and recent execution trace.</div>
        </div>
        <div class="actions">
          <button id="refresh" type="button">Refresh</button>
        </div>
      </div>
    </header>
    <div id="stats" class="tiles"></div>
    <div id="view"></div>
  </main>
  <script>
    const viewMeta = {
      overview: ["Overview", "Harness health, memory state, and recent execution trace."],
      memory: ["Memory", "Semantic facts and episodic records used by the deep agent."],
      chat: ["Chat", "Recent Telegram/user-assistant rows persisted by the harness."],
      traces: ["Traces", "JSONL event tail: route, memory retrieval, deep jobs, errors."],
      config: ["Config", "Runtime configuration, decision model, stickers, memory, and replies."],
      ops: ["Ops", "Local dashboard controls and baseline architecture notes."]
    };
    let state = { view: "overview", data: null, factDraft: { subject: "", content: "" }, configTab: "runtime" };
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const fmt = (s) => s ? esc(String(s).replace("T", " ").replace("Z", " UTC")) : "";
    const traceKind = (t) => t.kind || t.event || t.type || "event";
    const traceBadgeClass = (kind) => /error|fail/i.test(kind) ? "bad" : /memory|write|retrieval/i.test(kind) ? "ok" : /route|deep|turn/i.test(kind) ? "warn" : "";
    function activeView() {
      const raw = (location.hash || "#overview").replace("#", "");
      return viewMeta[raw] ? raw : "overview";
    }
    function setChrome() {
      state.view = activeView();
      const [title, subtitle] = viewMeta[state.view];
      document.getElementById("title").textContent = title;
      document.getElementById("subtitle").textContent = subtitle;
      document.querySelectorAll(".rail a").forEach(a => a.classList.toggle("on", a.dataset.view === state.view));
    }
    function statTile(label, value, note) {
      return `<div class="tile"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div><div class="meta">${esc(note)}</div></div>`;
    }
    function renderStats(data) {
      const counts = data.memory.counts;
      document.getElementById("stats").innerHTML = [
        statTile("Facts", counts.facts, "semantic memory"),
        statTile("Episodes", counts.episodes, "episodic memory"),
        statTile("Chat rows", counts.chat_log, "persisted dialog"),
        statTile("Trace events", data.traces.length, "latest tail")
      ].join("");
      document.getElementById("n-memory").textContent = counts.facts + counts.episodes;
      document.getElementById("n-chat").textContent = counts.chat_log;
      document.getElementById("n-traces").textContent = data.traces.length;
      document.getElementById("n-overview").textContent = "";
      document.getElementById("n-ops").textContent = "";
      document.getElementById("n-config").textContent = Object.keys(data.config?.overrides || {}).length || "";
    }
    function factRecord(f) {
      return `<div class="record">
        <div class="record-head">
          <div class="record-title">${esc(f.subject)}</div>
          <span class="badge ok">fact:${esc(f.id)}</span>
          <div class="record-actions"><button class="danger" data-delete-fact="${esc(f.id)}" type="button">Delete</button></div>
        </div>
        <div class="bodytext">${esc(f.content)}</div>
        <div class="meta">${esc(f.source)} | ${fmt(f.created_at)}</div>
      </div>`;
    }
    function episodeRecord(e) {
      return `<div class="record">
        <div class="record-head">
          <div class="record-title">Episode</div>
          <span class="badge warn">episode:${esc(e.id)}</span>
        </div>
        <div class="bodytext">${esc(e.summary)}</div>
        <div class="meta">${fmt(e.happened_at)} | ${esc(e.source)}</div>
      </div>`;
    }
    function chatRecord(c) {
      const cls = c.role === "assistant" ? "ok" : "";
      return `<div class="record">
        <div class="record-head">
          <div class="record-title">${esc(c.role)}</div>
          <span class="badge ${cls}">${esc(c.source || "chat")}</span>
        </div>
        <div class="bodytext">${esc(c.content)}</div>
        <div class="meta">${esc(c.session_id)} | ${fmt(c.created_at)}</div>
      </div>`;
    }
    function traceRecord(t) {
      const kind = traceKind(t);
      return `<div class="record">
        <div class="record-head">
          <div class="record-title">${esc(kind)}</div>
          <span class="badge ${traceBadgeClass(kind)}">${esc(t.trace_id || t.turn_id || "trace")}</span>
        </div>
        <pre class="trace">${esc(JSON.stringify(t, null, 2))}</pre>
      </div>`;
    }
    function empty(label) {
      return `<div class="empty">${esc(label)}</div>`;
    }
    function eventTime(event) {
      const ts = Date.parse(event.timestamp || "");
      return Number.isFinite(ts) ? ts : 0;
    }
    function latestTurnEvents(traces) {
      const groups = new Map();
      (traces || []).forEach(event => {
        const id = event.turn_id || event.trace_id;
        if (!id) return;
        if (!groups.has(id)) groups.set(id, []);
        groups.get(id).push(event);
      });
      let selected = [];
      let selectedTime = -1;
      groups.forEach(events => {
        const maxTime = Math.max(...events.map(eventTime));
        if (maxTime > selectedTime) {
          selected = events.slice().sort((a, b) => eventTime(a) - eventTime(b));
          selectedTime = maxTime;
        }
      });
      return selected;
    }
    function routeText(event) {
      return String(event.data?.route || event.data?.target || event.data?.decision || "");
    }
    function graphParts(nodes = [], flows = [], label = "", active = true) {
      return {
        nodes: new Set(nodes),
        flows: new Set(flows),
        label,
        active
      };
    }
    function replyFlowsForRoute(route, fallback = "router-reply") {
      const normalized = String(route || "").toLowerCase();
      if (normalized.includes("fast")) return ["fast-reply", "reply-trace"];
      if (normalized.includes("deep") || normalized.includes("delayed")) return ["loop-reply", "reply-trace"];
      if (normalized.includes("local") || normalized.includes("busy")) return ["router-reply", "reply-trace"];
      return [fallback, "reply-trace"];
    }
    function eventGraphParts(event) {
      const kind = traceKind(event);
      const route = routeText(event);
      if (event.type === "turn_start") {
        return graphParts(["gateway"], ["gateway-router"], "message in");
      }
      if (kind === "route_decision") {
        const flows = ["gateway-router"];
        if (route.includes("local") || route.includes("busy")) flows.push("router-reply");
        else if (route.includes("fast")) flows.push("router-fast");
        else flows.push("router-memory");
        return graphParts(["router"], flows, `route: ${route || "deep"}`);
      }
      if (kind === "fast_triage_started") {
        return graphParts(["fast"], ["router-fast"], "fast triage");
      }
      if (kind === "fast_triage_finished") {
        const decision = route || "reply_now";
        const flows = ["router-fast"];
        if (decision.includes("deep")) flows.push("fast-memory");
        else flows.push("fast-reply");
        return graphParts(["fast"], flows, `fast: ${decision}`);
      }
      if (kind === "fast_triage_fallback_to_deep") {
        return graphParts(["fast", "memory_gate"], ["router-fast", "fast-memory"], "fast fallback");
      }
      if (kind === "deep_job_queued") {
        return graphParts(["memory_gate", "loop"], ["router-memory"], "deep queued");
      }
      if (kind === "deep_job_started" || kind === "deep_agent_call_started") {
        return graphParts(["memory_gate", "loop", "deep"], ["router-memory", "memory-loop"], "loop running");
      }
      if (kind === "memory_retrieval") {
        return graphParts(
          ["memory_gate", "memory_records", "loop", "deep"],
          ["router-memory", "memory-read", "memory-loop"],
          "memory context"
        );
      }
      if (kind.includes("tool")) {
        return graphParts(["loop", "tools"], ["loop-tools"], "tool slot");
      }
      if (kind === "deep_agent_call_finished") {
        return graphParts(["loop", "deep", "reply"], ["memory-loop", "loop-reply"], "deep finished");
      }
      if (kind === "wait_reply_delivered") {
        return graphParts(["reply"], ["router-reply"], "wait reply");
      }
      if (kind === "memory_write_chat_log") {
        return graphParts(["trace"], ["reply-trace"], "chat log write", false);
      }
      if (kind === "memory_write_episode") {
        return graphParts(["memory_records", "trace"], ["reply-trace"], "episode write", false);
      }
      if (kind.includes("memory")) {
        return graphParts(["memory_gate", "memory_records"], ["memory-read"], kind);
      }
      if (kind.includes("reply") || event.type === "turn_end") {
        const flows = replyFlowsForRoute(route);
        return graphParts(["reply", "trace"], flows, event.type === "turn_end" ? "turn end" : kind);
      }
      if (/error|fail/i.test(kind) || event.status === "error") {
        const fallback = kind.includes("deep") ? "loop-reply" : "router-reply";
        return graphParts(["reply", "trace"], replyFlowsForRoute(route, fallback), "error");
      }
      return graphParts([], [], kind, false);
    }
    function analyzeTurn(events) {
      let deepRunning = false;
      let memorySeen = false;
      let toolRunning = false;
      let active = null;
      events.forEach(event => {
        const kind = traceKind(event);
        const parts = eventGraphParts(event);
        if (parts.active) active = parts;
        if (kind === "memory_retrieval") memorySeen = true;
        if (kind === "deep_agent_call_started" || kind === "deep_job_started") deepRunning = true;
        if (kind === "deep_agent_call_finished" || event.type === "turn_end" || kind === "deep_agent_error") deepRunning = false;
        if (kind.includes("tool_start")) toolRunning = true;
        if (kind.includes("tool_end") || event.type === "turn_end") toolRunning = false;
      });
      if (toolRunning) {
        return graphParts(["loop", "tools"], ["loop-tools"], "tool running");
      }
      if (deepRunning) {
        const nodes = ["memory_gate", "loop", "deep"];
        const flows = ["router-memory", "memory-loop"];
        if (memorySeen) {
          nodes.push("memory_records");
          flows.push("memory-read");
        }
        return graphParts(nodes, flows, memorySeen ? "loop using memory" : "loop running");
      }
      return active;
    }
    function replayTurnPath(events) {
      const nodes = new Set();
      const flows = new Set();
      events.forEach(event => {
        const parts = eventGraphParts(event);
        parts.nodes.forEach(node => nodes.add(node));
        parts.flows.forEach(flow => flows.add(flow));
      });
      const label = flows.has("loop-reply")
        ? "deep loop path"
        : flows.has("fast-reply")
          ? "fast reply path"
          : flows.has("router-reply")
            ? "router reply path"
            : "turn path";
      return { nodes, flows, label, active: true };
    }
    function graphState(data) {
      const events = latestTurnEvents(data.traces);
      const doneNodes = new Set();
      const doneFlows = new Set();
      events.forEach(event => {
        const parts = eventGraphParts(event);
        parts.nodes.forEach(node => doneNodes.add(node));
        parts.flows.forEach(flow => doneFlows.add(flow));
      });
      const latest = events[events.length - 1] || null;
      const ended = events.some(event => event.type === "turn_end");
      const ageMs = latest ? Date.now() - eventTime(latest) : Infinity;
      const recentEnded = ended && ageMs < 45 * 1000;
      const stale = latest && !ended && ageMs > 15 * 60 * 1000;
      const failed = events.some(event => /error|fail/i.test(traceKind(event)) || event.status === "error");
      const active = latest && !stale ? (!ended ? analyzeTurn(events) : recentEnded ? replayTurnPath(events) : null) : null;
      return {
        events,
        latest,
        doneNodes,
        doneFlows,
        hotNodes: active ? active.nodes : new Set(),
        liveFlows: active ? active.flows : new Set(),
        activeLabel: active?.label || "",
        live: Boolean(active),
        replay: Boolean(recentEnded && active),
        stale: Boolean(stale),
        failed
      };
    }
    function nodeClass(graph, id, extra = "") {
      const classes = ["node"];
      if (extra) classes.push(extra);
      if (graph.doneNodes.has(id)) classes.push("done");
      if (graph.hotNodes.has(id)) classes.push(graph.failed ? "bad" : "hot");
      return classes.join(" ");
    }
    function zoneClass(graph, id) {
      const classes = ["zone"];
      if (graph.doneNodes.has(id)) classes.push("done");
      if (graph.hotNodes.has(id)) classes.push(graph.failed ? "bad" : "hot");
      return classes.join(" ");
    }
    function flowClass(graph, id, extra = "") {
      const classes = ["run-flow"];
      if (extra) classes.push(extra);
      if (graph.doneFlows.has(id)) classes.push("done");
      if (graph.liveFlows.has(id)) classes.push("live");
      return classes.join(" ");
    }
    function graphNode(graph, id, x, y, title, subtitle, options = {}) {
      const w = options.w || 132;
      const h = options.h || 64;
      return `<g class="${nodeClass(graph, id, options.cls || "")}" transform="translate(${x} ${y})">
        <rect width="${w}" height="${h}" rx="7"></rect>
        <text x="14" y="26">${esc(title)}</text>
        <text class="subt" x="14" y="46">${esc(subtitle)}</text>
      </g>`;
    }
    function graphGateNode(graph, id, cx, cy, title, subtitle) {
      return `<g class="${nodeClass(graph, id, "gate-node")}">
        <path d="M${cx} ${cy - 44} L${cx + 76} ${cy} L${cx} ${cy + 44} L${cx - 76} ${cy} Z"></path>
        <text x="${cx}" y="${cy - 5}" text-anchor="middle">${esc(title)}</text>
        <text class="subt" x="${cx}" y="${cy + 15}" text-anchor="middle">${esc(subtitle)}</text>
      </g>`;
    }
    function runtimeGraph(data) {
      const graph = graphState(data);
      const latest = graph.latest;
      const latestKind = latest ? traceKind(latest) : "no trace";
      const status = !latest
        ? "No runs yet"
        : graph.live && !graph.replay
          ? `Running - ${graph.activeLabel || latestKind}`
          : graph.replay
            ? `Recent path - ${graph.activeLabel || latestKind}`
          : graph.stale
            ? `Stale - ${latestKind}`
            : graph.failed
              ? `Error - ${latestKind}`
              : `Finished - ${latestKind}`;
      const turn = latest?.turn_id || latest?.trace_id || "";
      return `<div class="graph-status">
        <div><span class="${graph.live && !graph.replay ? "live-dot" : ""}"></span><span class="badge ${graph.failed ? "bad" : graph.live && !graph.replay ? "warn" : "ok"}">${esc(status)}</span></div>
        <div class="meta">${esc(turn)}${graph.events.length ? ` - ${graph.events.length} event(s)` : ""}</div>
      </div>
      <div class="graph-card">
        <svg class="run-graph" viewBox="0 0 1040 470" role="img" aria-label="Niko harness architecture and live runtime state">
          <defs>
            <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L0,6 L9,3 z" fill="currentColor"></path>
            </marker>
          </defs>
          <rect class="zone" x="12" y="14" width="1016" height="436" rx="14"></rect>
          <text class="zone-label" x="28" y="38">NIKO HARNESS - clean turn flow, memory gate, loop, ops observer</text>

          <rect class="${zoneClass(graph, "loop")}" x="604" y="214" width="224" height="180" rx="12"></rect>
          <text class="zone-label" x="622" y="238">LOOP</text>
          <rect class="zone" x="272" y="334" width="280" height="112" rx="12"></rect>
          <text class="zone-label" x="290" y="358">MEMORY</text>

          <path class="${flowClass(graph, "gateway-router")}" d="M176 128 L222 128"></path>
          <path class="${flowClass(graph, "router-fast")}" d="M354 128 L400 128"></path>
          <path class="${flowClass(graph, "fast-reply")}" d="M532 128 L840 128"></path>
          <path class="${flowClass(graph, "router-reply", "dash")}" d="M354 164 L840 164"></path>

          <path class="${flowClass(graph, "router-memory")}" d="M288 160 L288 260 L322 260"></path>
          <path class="${flowClass(graph, "fast-memory", "dash")}" d="M466 160 L466 214 L454 232"></path>
          <path class="${flowClass(graph, "memory-read", "dash")}" d="M412 304 L412 374"></path>
          <path class="${flowClass(graph, "memory-loop")}" d="M490 260 L604 286"></path>
          <path class="${flowClass(graph, "loop-tools")}" d="M716 300 L716 326"></path>
          <path class="${flowClass(graph, "loop-reply")}" d="M828 286 L886 160"></path>
          <path class="${flowClass(graph, "reply-trace", "dash")}" d="M906 160 L906 340"></path>

          ${graphNode(graph, "gateway", 44, 96, "Gateway", "Telegram in/out")}
          ${graphNode(graph, "router", 222, 96, "Router", "local / fast / deep")}
          ${graphNode(graph, "fast", 400, 96, "Fast Agent", "triage / quick reply")}
          ${graphGateNode(graph, "memory_gate", 412, 260, "Memory Gate", "retrieve context")}
          ${graphNode(graph, "deep", 650, 256, "Deep Agent", "fcc-claude local")}
          ${graphNode(graph, "tools", 650, 326, "Tool Slot", "next iteration", {cls: "future"})}
          ${graphNode(graph, "memory_records", 326, 374, "Memory Records", "semantic + episodic", {w: 172})}
          ${graphNode(graph, "reply", 840, 96, "Reply", "send back")}
          ${graphNode(graph, "trace", 840, 340, "Trace / Ops", "JSONL + dashboard")}
        </svg>
      </div>`;
    }
    function renderOverview(data) {
      const facts = data.memory.facts.slice(0, 3).map(factRecord).join("") || empty("No semantic facts yet.");
      const episodes = data.memory.episodes.slice(0, 3).map(episodeRecord).join("") || empty("No episodic records yet.");
      const traces = data.traces.slice(0, 4).map(traceRecord).join("") || empty("No trace events yet.");
      return `<div class="panel">
        <h2>Live Harness Graph</h2>
        ${runtimeGraph(data)}
      </div>
      <div class="grid">
        <section class="panel"><h2>Semantic Preview</h2><div class="records">${facts}</div></section>
        <section class="panel"><h2>Episodic Preview</h2><div class="records">${episodes}</div></section>
      </div>
      <section class="panel"><h2>Trace Tail</h2><div class="records">${traces}</div></section>`;
    }
    function renderMemory(data) {
      const facts = data.memory.facts.map(factRecord).join("") || empty("No semantic facts yet.");
      const episodes = data.memory.episodes.map(episodeRecord).join("") || empty("No episodic records yet.");
      return `<div class="grid">
        <section class="panel">
          <h2>Add Semantic Fact</h2>
          <form id="fact-form" class="form-grid">
            <input id="fact-subject" placeholder="Subject" value="${esc(state.factDraft.subject)}">
            <textarea id="fact-content" placeholder="Fact content">${esc(state.factDraft.content)}</textarea>
            <button class="primary" type="submit">Add Fact</button>
          </form>
        </section>
        <section class="panel">
          <h2>Memory Contract</h2>
          <div class="records">
            <div class="record"><div class="splitline"><span>Semantic</span><span class="badge ok">facts</span></div><div class="meta">Stable facts, preferences, rules, and project knowledge.</div></div>
            <div class="record"><div class="splitline"><span>Episodic</span><span class="badge warn">events</span></div><div class="meta">Dated turns, task outcomes, and follow-ups while deep jobs run.</div></div>
          </div>
        </section>
      </div>
      <div class="grid">
        <section class="panel"><h2>Semantic Facts</h2><div class="records">${facts}</div></section>
        <section class="panel"><h2>Episodic Events</h2><div class="records">${episodes}</div></section>
      </div>`;
    }
    function renderChat(data) {
      const chat = data.memory.chat_log.map(chatRecord).join("") || empty("No chat rows yet.");
      return `<section class="panel"><h2>Recent Chat</h2><div class="records">${chat}</div></section>`;
    }
    function renderTraces(data) {
      const traces = data.traces.map(traceRecord).join("") || empty("No trace events yet.");
      return `<section class="panel"><h2>Trace Events</h2><div class="records">${traces}</div></section>`;
    }
    function configSections(data) {
      return data.config?.sections || [];
    }
    function currentConfigSection(data) {
      const sections = configSections(data);
      if (!sections.length) return null;
      return sections.find(section => section.id === state.configTab) || sections[0];
    }
    function boolValue(value) {
      return ["1", "true", "yes", "on"].includes(String(value ?? "").toLowerCase());
    }
    function configFieldInput(field) {
      const disabled = field.editable ? "" : " disabled";
      const value = field.value ?? "";
      if (field.type === "bool") {
        return `<div class="checkline"><input data-config-key="${esc(field.name)}" type="checkbox"${boolValue(value) ? " checked" : ""}${disabled}><span>${boolValue(value) ? "Enabled" : "Disabled"}</span></div>`;
      }
      if (field.type === "select") {
        const options = (field.choices || []).map(choice => `<option value="${esc(choice)}"${String(choice) === String(value) ? " selected" : ""}>${esc(choice)}</option>`).join("");
        return `<select data-config-key="${esc(field.name)}"${disabled}>${options}</select>`;
      }
      if (field.type === "textarea") {
        return `<textarea data-config-key="${esc(field.name)}"${disabled}>${esc(value)}</textarea>`;
      }
      return `<input data-config-key="${esc(field.name)}" type="${field.type === "number" ? "number" : "text"}" value="${esc(value)}"${disabled}>`;
    }
    function configField(field) {
      const locked = field.editable ? "" : " OS env lock";
      const runtimeValue = field.runtime_value == null ? "" : ` | runtime=${field.runtime_value}`;
      return `<div class="field">
        <label><span>${esc(field.label)}</span><span class="source">${esc(field.source)}${esc(locked)}</span></label>
        ${configFieldInput(field)}
        <div class="source">${esc(field.name)} | default=${esc(field.default ?? "")}${esc(runtimeValue)}</div>
      </div>`;
    }
    function renderBotControls(bot) {
      const running = Boolean(bot?.running);
      const managed = Boolean(bot?.managed);
      const badge = running ? "ok" : managed ? "warn" : "";
      const label = running ? `running pid=${bot.pid}` : managed ? `stopped code=${bot.returncode}` : "not managed";
      return `<section class="panel">
        <h2>Telegram Bot</h2>
        <div class="records">
          <div class="record">
            <div class="splitline"><span>Managed process</span><span class="badge ${badge}">${esc(label)}</span></div>
            <div class="meta">Dashboard chỉ quản lý bot do chính dashboard start. Bot đang chạy ở terminal riêng cần dừng ở terminal đó.</div>
          </div>
        </div>
        <div class="actions" style="margin-left:0">
          <button class="primary" data-bot-action="start" type="button"${running ? " disabled" : ""}>Start bot</button>
          <button class="danger" data-bot-action="stop" type="button"${running ? "" : " disabled"}>Stop bot</button>
        </div>
      </section>`;
    }
    function renderConfig(data) {
      const sections = configSections(data);
      const active = currentConfigSection(data);
      if (!active) return empty("No config schema available.");
      state.configTab = active.id;
      const tabs = sections.map(section => `<button data-config-tab="${esc(section.id)}" class="${section.id === active.id ? "on" : ""}" type="button">${esc(section.title)}</button>`).join("");
      const fields = active.fields.map(configField).join("");
      return `${active.id === "runtime" ? renderBotControls(data.config?.bot || {}) : ""}
      <section class="panel">
        <h2>Config</h2>
        <div class="tabs">${tabs}</div>
        <form id="config-form" data-section="${esc(active.id)}" class="form-grid">
          <div class="record">
            <div class="splitline"><span>${esc(active.title)}</span><span class="badge warn">${esc(data.config?.path || "")}</span></div>
            <div class="meta">${esc(active.description || "")}</div>
          </div>
          <div class="field-grid">${fields}</div>
          <div class="actions" style="margin-left:0">
            <button class="primary" type="submit">Save</button>
            <button data-config-reset="${esc(active.id)}" type="button">Reset tab</button>
          </div>
        </form>
      </section>`;
    }
    function renderOps(data) {
      return `<div class="grid">
        <section class="panel">
          <h2>Endpoints</h2>
          <div class="records">
            <div class="record"><span class="badge ok">GET</span> /api/snapshot</div>
            <div class="record"><span class="badge ok">GET</span> /api/config</div>
            <div class="record"><span class="badge ok">GET</span> /api/runtime/bot</div>
            <div class="record"><span class="badge ok">GET</span> /api/traces</div>
            <div class="record"><span class="badge ok">GET</span> /api/memory</div>
            <div class="record"><span class="badge warn">POST</span> /api/config</div>
            <div class="record"><span class="badge warn">POST</span> /api/runtime/bot/start|stop</div>
            <div class="record"><span class="badge warn">POST</span> /api/memory/facts</div>
            <div class="record"><span class="badge bad">DELETE</span> /api/memory/facts/{id}</div>
          </div>
        </section>
        <section class="panel">
          <h2>Baseline Boundary</h2>
          <div class="records">
            <div class="record">This dashboard observes the harness. It does not participate in the agent loop.</div>
            <div class="record">SQLite and JSONL are the baseline store before the lakehouse and graph layer.</div>
          </div>
        </section>
      </div>`;
    }
    function render() {
      if (!state.data) return;
      setChrome();
      renderStats(state.data);
      const view = document.getElementById("view");
      if (state.view === "memory") view.innerHTML = renderMemory(state.data);
      else if (state.view === "chat") view.innerHTML = renderChat(state.data);
      else if (state.view === "traces") view.innerHTML = renderTraces(state.data);
      else if (state.view === "config") view.innerHTML = renderConfig(state.data);
      else if (state.view === "ops") view.innerHTML = renderOps(state.data);
      else view.innerHTML = renderOverview(state.data);
      bindFactForm();
      bindConfigForm();
    }
    function factFormHasFocus() {
      const form = document.getElementById("fact-form");
      return Boolean(form && form.contains(document.activeElement));
    }
    function configFormHasFocus() {
      const form = document.getElementById("config-form");
      return Boolean(form && form.contains(document.activeElement));
    }
    async function loadSnapshot(options = {}) {
      const res = await fetch("/api/snapshot");
      state.data = await res.json();
      if ((!factFormHasFocus() && !configFormHasFocus()) || options.forceRender) {
        render();
      }
    }
    function bindFactForm() {
      const form = document.getElementById("fact-form");
      if (!form || form.dataset.bound) return;
      form.dataset.bound = "1";
      const subject = document.getElementById("fact-subject");
      const content = document.getElementById("fact-content");
      subject.addEventListener("input", () => { state.factDraft.subject = subject.value; });
      content.addEventListener("input", () => { state.factDraft.content = content.value; });
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        await fetch("/api/memory/facts", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            subject: subject.value,
            content: content.value,
            source: "ops"
          })
        });
        state.factDraft = { subject: "", content: "" };
        await loadSnapshot({ forceRender: true });
      });
    }
    function bindConfigForm() {
      document.querySelectorAll("[data-config-tab]").forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = "1";
        button.addEventListener("click", () => {
          state.configTab = button.dataset.configTab;
          render();
        });
      });
      document.querySelectorAll("[data-bot-action]").forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = "1";
        button.addEventListener("click", async () => {
          await fetch(`/api/runtime/bot/${button.dataset.botAction}`, { method: "POST" });
          await loadSnapshot({ forceRender: true });
        });
      });
      document.querySelectorAll("[data-config-reset]").forEach(button => {
        if (button.dataset.bound) return;
        button.dataset.bound = "1";
        button.addEventListener("click", async () => {
          await fetch("/api/config/reset", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({section: button.dataset.configReset})
          });
          await loadSnapshot({ forceRender: true });
        });
      });
      const form = document.getElementById("config-form");
      if (!form || form.dataset.bound) return;
      form.dataset.bound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const values = {};
        form.querySelectorAll("[data-config-key]").forEach(input => {
          if (input.disabled) return;
          values[input.dataset.configKey] = input.type === "checkbox" ? input.checked : input.value;
        });
        const res = await fetch("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({values})
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({error: "Config save failed"}));
          alert(data.error || "Config save failed");
          return;
        }
        await loadSnapshot({ forceRender: true });
      });
    }
    document.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-delete-fact]");
      if (!button) return;
      await fetch(`/api/memory/facts/${button.dataset.deleteFact}`, { method: "DELETE" });
      await loadSnapshot({ forceRender: true });
    });
    document.getElementById("refresh").addEventListener("click", () => loadSnapshot({ forceRender: true }));
    window.addEventListener("hashchange", render);
    setChrome();
    loadSnapshot();
    setInterval(loadSnapshot, 5000);
  </script>
</body>
</html>
"""


def json_bytes(payload: dict[str, Any], status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"), "application/json"


def html_bytes(text: str) -> tuple[int, bytes, str]:
    return 200, text.encode("utf-8"), "text/html; charset=utf-8"


def make_handler(
    memory_store: MemoryStore | None = None,
    trace_logger: TraceLogger | None = None,
    bot_manager: TelegramBotProcessManager | None = None,
):
    store = memory_store or default_memory_store()
    traces = trace_logger or default_trace_logger()
    manager = bot_manager or TelegramBotProcessManager()

    class NikoOpsHandler(BaseHTTPRequestHandler):
        server_version = "NikoOps/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(*html_bytes(INDEX_HTML))
                return
            if parsed.path == "/api/snapshot":
                self._send(
                    *json_bytes(
                        {
                            "memory": store.snapshot(),
                            "traces": traces.read_events(limit=80),
                            "config": config_snapshot(manager),
                        }
                    )
                )
                return
            if parsed.path == "/api/config":
                self._send(*json_bytes({"config": config_snapshot(manager)}))
                return
            if parsed.path == "/api/runtime/bot":
                self._send(*json_bytes({"bot": manager.status()}))
                return
            if parsed.path == "/api/traces":
                limit = self._query_limit(parsed.query)
                self._send(*json_bytes({"traces": traces.read_events(limit=limit)}))
                return
            if parsed.path == "/api/memory":
                self._send(*json_bytes({"memory": store.snapshot()}))
                return
            self._send(*json_bytes({"error": "not found"}, status=404))

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/memory/facts":
                data = self._read_json()
                try:
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
            if parsed.path == "/api/config":
                data = self._read_json()
                values = data.get("values") if isinstance(data.get("values"), dict) else data
                try:
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
            self._send(*json_bytes({"error": "not found"}, status=404))

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            prefix = "/api/memory/facts/"
            if parsed.path.startswith(prefix):
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
            return

        def _query_limit(self, query: str) -> int:
            params = parse_qs(query)
            try:
                return max(1, min(1000, int(params.get("limit", ["200"])[0])))
            except ValueError:
                return 200

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}

        def _send(self, status: int, body: bytes, content_type: str) -> None:
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
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(memory_store, trace_logger, bot_manager))


def main() -> int:
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
