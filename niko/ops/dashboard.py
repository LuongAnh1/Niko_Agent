from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any

from niko.config import env_value, load_env_files
from niko.harness.trace import TraceLogger, default_trace_logger
from niko.memory.store import MemoryStore, default_memory_store


DEFAULT_OPS_HOST = "127.0.0.1"
DEFAULT_OPS_PORT = 7777


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
      ops: ["Ops", "Local dashboard controls and baseline architecture notes."]
    };
    let state = { view: "overview", data: null, factDraft: { subject: "", content: "" } };
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
    function renderOps(data) {
      return `<div class="grid">
        <section class="panel">
          <h2>Endpoints</h2>
          <div class="records">
            <div class="record"><span class="badge ok">GET</span> /api/snapshot</div>
            <div class="record"><span class="badge ok">GET</span> /api/traces</div>
            <div class="record"><span class="badge ok">GET</span> /api/memory</div>
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
      else if (state.view === "ops") view.innerHTML = renderOps(state.data);
      else view.innerHTML = renderOverview(state.data);
      bindFactForm();
    }
    function factFormHasFocus() {
      const form = document.getElementById("fact-form");
      return Boolean(form && form.contains(document.activeElement));
    }
    async function loadSnapshot(options = {}) {
      const res = await fetch("/api/snapshot");
      state.data = await res.json();
      if (!factFormHasFocus() || options.forceRender) {
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


def make_handler(memory_store: MemoryStore | None = None, trace_logger: TraceLogger | None = None):
    store = memory_store or default_memory_store()
    traces = trace_logger or default_trace_logger()

    class NikoOpsHandler(BaseHTTPRequestHandler):
        server_version = "NikoOps/0.1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(*html_bytes(INDEX_HTML))
                return
            if parsed.path == "/api/snapshot":
                self._send(*json_bytes({"memory": store.snapshot(), "traces": traces.read_events(limit=80)}))
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
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(memory_store, trace_logger))


def main() -> int:
    load_env_files()
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
