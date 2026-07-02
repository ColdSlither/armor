"""
ARMOR serve.py — Minimal HTTP API + single-page GUI for Atlas.

Inspired by PewDiePie's Odysseus (https://github.com/pewdiepie-archdaemon/odysseus).

ARMOR is the Iron Man suit. Atlas (github.com/ColdSlither/atlas) is the arc reactor.
ARMOR never imports Atlas directly — it shells out to the `atlas` CLI.
Atlas doesn't know ARMOR exists.

Usage:
    python3 serve.py                  # http://127.0.0.1:9090
    python3 serve.py --port 9090
    python3 serve.py --atlas ../atlas # path to Atlas repo
"""

from __future__ import annotations

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


# ── Config ──

DEFAULT_PORT = 9092
ATLAS_CLI = "atlas"  # or a full path like "../atlas/agent.py"


# ── JSON helpers ──

def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, default=str) + "\n").encode("utf-8")


def stream_event(event: str, **fields: Any) -> bytes:
    return json_bytes({"event": event, **fields})


# ── Atlas CLI wrapper ──

def _atlas(*args: str, timeout: int = 300, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run an Atlas CLI command and return the result."""
    cmd = [sys.executable, str(ATLAS_CLI)] if Path(ATLAS_CLI).is_file() else [ATLAS_CLI]
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or str(Path.cwd()),
    )


# ── MIME types ──

MIME_JSON = "application/json"
MIME_HTML = "text/html; charset=utf-8"
MIME_STREAM = "application/x-ndjson"


# ── ARMOR HTML — Cyberpunk / Odysseus-inspired ──

ARMOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARMOR</title>
<style>
  :root {
    --bg-deep:     #050a0a;
    --bg-surface:  #0a1212;
    --bg-sidebar:  #000000;
    --bg-card:     rgba(10, 18, 18, 0.88);
    --fg:          #c8d8d8;
    --fg-dim:      #4a6a6a;
    --teal:        #00e5ff;
    --amber:       #ff8c00;
    --green:       #22c55e;
    --red:         #ff3355;
    --yellow:      #e5ff00;
    --border:      #0d2222;
    --border-dim:  #081515;
    --glow-teal:   0 0 12px rgba(0, 229, 255, 0.25);
    --glow-green:  0 0 12px rgba(34, 197, 94, 0.25);
    --glow-amber:  0 0 12px rgba(255, 140, 0, 0.25);
    --font-mono:   'JetBrains Mono','Fira Code','Cascadia Code',monospace;
    --font-ui:     system-ui,-apple-system,'Segoe UI',sans-serif;
    --radius:      6px;
    --transition:  0.2s cubic-bezier(0.4, 0, 0.2, 1);
    --sidebar-w:   200px;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  ::-webkit-scrollbar { width: 5px; height: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--fg-dim); }

  body {
    background: var(--bg-deep);
    color: var(--fg);
    font: 14px/1.6 var(--font-mono);
    height: 100vh;
    display: flex;
    overflow: hidden;
  }

  /* ── Sidebar ── */
  .sidebar {
    width: var(--sidebar-w);
    min-width: var(--sidebar-w);
    background: var(--bg-sidebar);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 0;
    position: relative;
  }
  .sidebar::after {
    content: '';
    position: absolute;
    right: -1px;
    top: 0;
    bottom: 0;
    width: 1px;
    background: linear-gradient(180deg, transparent, rgba(0, 229, 255, 0.15), transparent);
  }
  .sidebar-brand {
    padding: 1rem 1rem 0.5rem;
    border-bottom: 1px solid var(--border-dim);
    margin-bottom: 0.5rem;
  }
  .sidebar-brand h1 {
    font-size: 16px;
    font-weight: 500;
    color: var(--yellow);
    letter-spacing: 3px;
    text-transform: uppercase;
    font-family: var(--font-ui);
    text-shadow: 0 0 20px rgba(229, 255, 0, 0.12);
  }
  .sidebar-brand .tagline {
    font-size: 10px;
    color: var(--fg-dim);
    letter-spacing: 1px;
    text-transform: uppercase;
    font-family: var(--font-ui);
    margin-top: 0.1rem;
  }
  .sidebar-nav {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0.25rem 0.5rem;
  }
  .nav-item {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.55rem 0.7rem;
    border-radius: var(--radius);
    cursor: pointer;
    color: var(--fg-dim);
    font-size: 13px;
    font-family: var(--font-ui);
    font-weight: 500;
    transition: var(--transition);
    text-decoration: none;
    border: none;
    background: none;
    width: 100%;
    text-align: left;
  }
  .nav-item:hover { color: var(--fg); background: rgba(255,255,255,0.03); }
  .nav-item.active {
    color: var(--teal);
    background: rgba(0, 229, 255, 0.06);
  }
  .nav-item .icon {
    width: 22px;
    height: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
  }
  .nav-item .icon svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

  .sidebar-footer {
    padding: 0.5rem;
    border-top: 1px solid var(--border-dim);
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 11px;
    color: var(--fg-dim);
    font-family: var(--font-ui);
  }
  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px rgba(34,197,94,0.5);
    flex-shrink: 0;
  }
  .status-dot.idle { background: var(--fg-dim); box-shadow: none; }
  .status-dot.busy { background: var(--teal); box-shadow: 0 0 6px rgba(0,229,255,0.5); }
  .model-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Main content ── */
  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background:
      radial-gradient(ellipse 70% 50% at 50% -10%, rgba(0, 229, 255, 0.02) 0%, transparent 70%),
      repeating-linear-gradient(0deg, transparent, transparent 40px, rgba(0, 229, 255, 0.008) 40px, rgba(0, 229, 255, 0.008) 41px);
  }

  .page {
    flex: 1;
    display: none;
    flex-direction: column;
    padding: 1rem 1.2rem;
    overflow-y: auto;
    opacity: 0;
    transform: translateY(4px);
    transition: opacity 0.2s ease, transform 0.2s ease;
  }
  .page.active { display: flex; opacity: 1; transform: translateY(0); }

  /* ── Input row ── */
  .input-row {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 0.8rem;
  }
  .input-row input {
    flex: 1;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    color: var(--fg);
    font: 14px var(--font-mono);
    padding: 0.6rem 0.9rem;
    outline: none;
    border-radius: var(--radius);
    transition: var(--transition);
  }
  .input-row input::placeholder { color: var(--fg-dim); opacity: 0.4; }
  .input-row input:focus {
    border-color: var(--teal);
    box-shadow: var(--glow-teal);
  }
  .input-row button {
    background: linear-gradient(135deg, var(--green), #1a9e4e);
    color: #000;
    border: none;
    font: 12px var(--font-ui);
    font-weight: 600;
    padding: 0.6rem 1.2rem;
    border-radius: var(--radius);
    cursor: pointer;
    transition: var(--transition);
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .input-row button:hover:not(:disabled) { box-shadow: var(--glow-green); transform: translateY(-1px); }
  .input-row button:active:not(:disabled) { transform: translateY(0); }
  .input-row button:disabled { opacity: 0.25; cursor: not-allowed; }

  /* ── Pipeline header ── */
  .pipeline-bar {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    margin-bottom: 0.8rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--border-dim);
    font-family: var(--font-ui);
    font-size: 11px;
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .pstep {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    color: var(--fg-dim);
    transition: var(--transition);
  }
  .pstep .pn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px; height: 18px;
    border-radius: 50%;
    font-size: 10px;
    font-weight: 700;
    background: var(--border);
    color: var(--fg-dim);
    transition: var(--transition);
  }
  .pstep.active { color: var(--teal); }
  .pstep.active .pn { background: var(--teal); color: #000; box-shadow: var(--glow-teal); }
  .pstep.done { color: var(--green); }
  .pstep.done .pn { background: var(--green); color: #000; box-shadow: var(--glow-green); }
  .parrow { color: var(--border); font-size: 12px; }

  /* ── Output / chat areas ── */
  #work-output, #chat-output {
    flex: 1;
    overflow-y: auto;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.7rem;
    font-size: 13px;
    backdrop-filter: blur(4px);
  }

  .dispatch {
    padding: 0.4rem 0.6rem;
    margin-bottom: 0.35rem;
    border-left: 3px solid var(--border);
    background: rgba(255,255,255,0.015);
    border-radius: 0 var(--radius) var(--radius) 0;
    animation: slidein 0.25s ease-out;
  }
  .dispatch:hover { background: rgba(255,255,255,0.03); }
  .dispatch .rd { color: var(--fg-dim); font-size: 11px; }
  .dispatch .findings { color: var(--fg-dim); font-size: 11px; margin-top: 0.15rem; }
  .dispatch.pass, .dispatch.approve { border-left-color: var(--green); }
  .dispatch.fail, .dispatch.request_changes { border-left-color: var(--amber); }
  .dispatch.error { border-left-color: var(--red); }
  .dispatch.complete {
    border-left-color: var(--teal);
    border-top: 1px solid var(--border);
    margin-top: 0.4rem;
    padding-top: 0.5rem;
  }
  .dispatch.complete strong { color: var(--teal); }

  @keyframes slidein {
    from { opacity: 0; transform: translateX(-6px); }
    to   { opacity: 1; transform: translateX(0); }
  }

  /* ── Chat messages ── */
  .chat-msg {
    margin-bottom: 0.6rem;
    padding: 0.4rem 0.6rem;
    border-radius: var(--radius);
    animation: slidein 0.25s ease-out;
  }
  .chat-msg .role {
    font-family: var(--font-ui);
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 0.2rem;
  }
  .chat-msg.user .role { color: var(--teal); }
  .chat-msg.assistant .role { color: var(--green); }
  .chat-msg .content { white-space: pre-wrap; line-height: 1.5; font-size: 13px; }
  .chat-msg .content code {
    background: rgba(0,229,255,0.06);
    border: 1px solid var(--border);
    padding: 0.05rem 0.25rem;
    border-radius: 3px;
    font-size: 12px;
    color: var(--green);
  }
  .chat-msg .content pre {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.6rem;
    margin: 0.4rem 0;
    overflow-x: auto;
    font-size: 12px;
  }
  .chat-msg .content pre code {
    background: none; border: none; padding: 0; color: var(--fg);
  }

  .thinking {
    display: flex;
    gap: 0.25rem;
    align-items: center;
    padding: 0.4rem 0;
    color: var(--fg-dim);
    font-size: 11px;
  }
  .thinking .td {
    width: 4px; height: 4px;
    border-radius: 50%;
    background: var(--teal);
    animation: tb 1.2s ease-in-out infinite;
  }
  .thinking .td:nth-child(2) { animation-delay: 0.2s; }
  .thinking .td:nth-child(3) { animation-delay: 0.4s; }
  @keyframes tb {
    0%,80%,100% { transform: scale(0.5); opacity: 0.3; }
    40% { transform: scale(1); opacity: 1; }
  }

  /* ── Models ── */
  #model-status {
    margin-bottom: 0.8rem;
    font-family: var(--font-ui);
    font-size: 11px;
    color: var(--fg-dim);
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  #model-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  #model-table th, #model-table td {
    text-align: left;
    padding: 0.5rem 0.7rem;
    border-bottom: 1px solid var(--border-dim);
  }
  #model-table th {
    color: var(--fg-dim);
    font-weight: 500;
    font-family: var(--font-ui);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  #model-table tbody tr { transition: var(--transition); }
  #model-table tbody tr:hover { background: rgba(0,229,255,0.02); }
  .ms {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-ui);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .ms.loaded {
    background: rgba(34,197,94,0.12);
    color: var(--green);
    box-shadow: 0 0 6px rgba(34,197,94,0.08);
  }
  .btn-swap {
    background: transparent;
    color: var(--teal);
    border: 1px solid rgba(0,229,255,0.25);
    font: 11px var(--font-ui);
    font-weight: 500;
    padding: 0.2rem 0.6rem;
    border-radius: 3px;
    cursor: pointer;
    transition: var(--transition);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .btn-swap:hover:not(:disabled) {
    background: rgba(0,229,255,0.08);
    box-shadow: var(--glow-teal);
    border-color: var(--teal);
  }
  .btn-swap:disabled { opacity: 0.3; cursor: not-allowed; }
  .spinner {
    display: inline-block;
    width: 12px; height: 12px;
    border: 2px solid var(--border);
    border-top-color: var(--teal);
    border-radius: 50%;
    animation: spin 0.6s linear infinite;
    vertical-align: middle;
    margin-right: 0.2rem;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<div class="sidebar">
  <div class="sidebar-brand">
    <h1>ARMOR</h1>
    <div class="tagline">&#9654; atlas shell</div>
  </div>
  <div class="sidebar-nav">
    <button class="nav-item active" onclick="showPage('work')">
      <span class="icon">
        <svg viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6-1.6 1.6"/><path d="M10.5 14.7a1 1 0 0 0 1.4 0l3-3"/><path d="M4 12h2"/><path d="M18 12h2"/><path d="M4 6h2"/><path d="M4 18h2"/><path d="M18 6h2"/><path d="M18 18h2"/></svg>
      </span>
      Work
    </button>
    <button class="nav-item" onclick="showPage('chat')">
      <span class="icon">
        <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      </span>
      Chat
    </button>
    <button class="nav-item" onclick="showPage('models')">
      <span class="icon">
        <svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      </span>
      Models
    </button>
  </div>
  <div class="sidebar-footer">
    <span class="status-dot" id="status-dot"></span>
    <span class="model-name" id="model-label">offline</span>
  </div>
</div>

<div class="main">

<div id="page-work" class="page active">
  <div class="input-row">
    <input id="work-input" placeholder="describe the task..." autofocus>
    <button id="work-btn" onclick="runWork()">go</button>
  </div>
  <div id="work-output">
    <div class="dispatch" style="border-left-color:var(--fg-dim);animation:none">
      <span class="rd">&#9654; pipeline idle — submit a task above</span>
    </div>
  </div>
</div>

<div id="page-chat" class="page">
  <div id="chat-output">
    <div class="chat-msg assistant" style="animation:none">
      <div class="role">Atlas</div>
      <div class="content">terminal online. ask anything.</div>
    </div>
  </div>
  <div class="input-row" style="margin-top:0.5rem;margin-bottom:0">
    <input id="chat-input" placeholder="ask something...">
    <button id="chat-btn" onclick="sendChat()">send</button>
  </div>
</div>

<div id="page-models" class="page">
  <div id="model-status">scanning...</div>
  <table id="model-table"><thead><tr>
    <th>Model</th><th>Description</th><th>Size</th><th></th>
  </tr></thead><tbody></tbody></table>
</div>

</div>

<script>
const API = window.location.origin;

// ── Status polling ──
async function pollStatus() {
  try {
    const resp = await fetch(API + '/api/status');
    const data = await resp.json();
    const dot = document.getElementById('status-dot');
    const label = document.getElementById('model-label');
    const m = data.model && data.model.loaded;
    if (m && m !== '(none)') {
      dot.className = 'status-dot';
      label.textContent = m.split('/').pop() || m;
    } else {
      dot.className = 'status-dot idle';
      label.textContent = 'offline';
    }
  } catch(e) {}
}
setInterval(pollStatus, 10000);
pollStatus();

// ── Page navigation ──
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(a => a.classList.remove('active'));
  const page = document.getElementById('page-' + name);
  page.classList.add('active');
  document.querySelector('.nav-item[onclick*="' + name + '"]').classList.add('active');
  // re-trigger transition
  page.style.transition = 'none';
  page.offsetHeight;
  page.style.transition = '';
  if (name === 'models') refreshModels();
  if (name === 'work') document.getElementById('work-input').focus();
  if (name === 'chat') document.getElementById('chat-input').focus();
}

// ── Pipeline bar ──
function buildPipelineBar() {
  const bar = document.createElement('div');
  bar.className = 'pipeline-bar';
  bar.innerHTML =
    '<div class="pstep" id="ps-editor"><span class="pn">E</span> Editor</div>' +
    '<span class="parrow">&rarr;</span>' +
    '<div class="pstep" id="ps-verifier"><span class="pn">V</span> Verifier</div>' +
    '<span class="parrow">&rarr;</span>' +
    '<div class="pstep" id="ps-reviewer"><span class="pn">R</span> Reviewer</div>';
  return bar;
}
function setStep(spec, state) {
  const el = document.getElementById('ps-' + spec);
  if (el) el.className = 'pstep ' + state;
}

// ── Work pipeline ──
async function runWork() {
  const input = document.getElementById('work-input');
  const btn = document.getElementById('work-btn');
  const spec = input.value.trim();
  if (!spec) return;
  const output = document.getElementById('work-output');
  output.innerHTML = '';
  output.appendChild(buildPipelineBar());
  btn.disabled = true;
  try {
    const resp = await fetch(API + '/api/work', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({spec, max_rounds: 3})
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const lines = buf.split('\\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const ev = JSON.parse(line);
          const div = document.createElement('div');
          if (ev.event === 'result') {
            div.className = 'dispatch ' + (ev.verdict || '');
            setStep(ev.specialist, 'done');
            const label = (ev.specialist || '').charAt(0).toUpperCase() + (ev.specialist || '').slice(1);
            div.innerHTML = '<span class="rd">round ' + (ev.round || 1) + '</span> ' +
              '<strong>' + label + '</strong> &mdash; <span class="verd">' + (ev.verdict || '?').toUpperCase() + '</span>';
            if (ev.findings) {
              const t = String(ev.findings);
              div.innerHTML += '<div class="findings">' + escapeHtml(t.length > 120 ? t.slice(0,120) + '...' : t) + '</div>';
            }
          } else if (ev.event === 'complete') {
            div.className = 'dispatch complete';
            div.innerHTML = '<strong>pipeline ' + escapeHtml(ev.status || 'complete').toUpperCase() + '</strong>';
          } else if (ev.event === 'error') {
            div.className = 'dispatch error';
            div.innerHTML = '<strong>ERROR</strong> ' + escapeHtml(ev.message || '');
          } else if (ev.event === 'dispatch') {
            setStep(ev.specialist, 'active');
          }
          if (div.innerHTML) output.appendChild(div);
        } catch(e) {}
      }
    }
    output.scrollTop = output.scrollHeight;
  } catch(e) {
    output.innerHTML = '<div class="dispatch error"><strong>NETWORK ERROR</strong> ' + escapeHtml(e.message) + '</div>';
  }
  btn.disabled = false;
}

// ── Chat ──
async function sendChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('chat-btn');
  const msg = input.value.trim();
  if (!msg) return;
  const output = document.getElementById('chat-output');
  const placeholder = output.querySelector('.placeholder');
  if (placeholder) placeholder.remove();

  const userDiv = document.createElement('div');
  userDiv.className = 'chat-msg user';
  userDiv.innerHTML = '<div class="role">you</div><div class="content">' + escapeHtml(msg) + '</div>';
  output.appendChild(userDiv);
  input.value = '';
  btn.disabled = true;

  const thinkDiv = document.createElement('div');
  thinkDiv.className = 'thinking';
  thinkDiv.innerHTML = '<span>thinking</span><span class="td"></span><span class="td"></span><span class="td"></span>';
  output.appendChild(thinkDiv);
  output.scrollTop = output.scrollHeight;

  const asstDiv = document.createElement('div');
  asstDiv.className = 'chat-msg assistant';
  asstDiv.style.display = 'none';
  asstDiv.innerHTML = '<div class="role">Atlas</div><div class="content"></div>';
  const contentDiv = asstDiv.querySelector('.content');

  try {
    const resp = await fetch(API + '/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let full = '';

    thinkDiv.remove();
    asstDiv.style.display = '';
    output.appendChild(asstDiv);

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const lines = buf.split('\\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const ev = JSON.parse(line);
          if (ev.event === 'token') { full += ev.content; contentDiv.textContent = full; }
          if (ev.event === 'done') { full = ev.content; contentDiv.textContent = full; }
        } catch(e) {}
      }
    }
  } catch(e) {
    thinkDiv.remove();
    asstDiv.style.display = '';
    output.appendChild(asstDiv);
    contentDiv.textContent = 'error: ' + e.message;
  }
  output.scrollTop = output.scrollHeight;
  btn.disabled = false;
}

// ── Models ──
async function refreshModels() {
  try {
    const resp = await fetch(API + '/api/models');
    const data = await resp.json();
    const lines = (data.output || '').split('\\n').filter(Boolean);
    const statusEl = document.getElementById('model-status');
    let loaded = '';
    for (const line of lines) {
      if (line.includes('*')) {
        loaded = line.replace(/[*]/g, '').trim().split(/\\s+/)[0];
      }
    }
    statusEl.innerHTML = loaded
      ? 'active: <span class="ms loaded">' + escapeHtml(loaded) + '</span>'
      : 'scanning models...';
    const tbody = document.querySelector('#model-table tbody');
    tbody.innerHTML = '';
    // Each line: model_name  desc  size  [* if loaded]
    for (const line of lines) {
      const parts = line.trim().split(/\\s{2,}/);
      const name = parts[0] || line.trim();
      const isLoaded = loaded && name.includes(loaded);
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(name) + '</td>' +
        '<td style="color:var(--fg-dim);font-size:11px">' + escapeHtml(parts[1] || '-') + '</td>' +
        '<td style="color:var(--fg-dim)">' + escapeHtml(parts[2] || '-') + '</td>' +
        '<td>' + (isLoaded
          ? '<span class="ms loaded">active</span>'
          : '<button class="btn-swap" onclick="swapModel(\'' + escapeAttr(name) + '\')">load</button>') + '</td>';
      tbody.appendChild(tr);
    }
  } catch(e) {
    document.getElementById('model-status').textContent = 'error: ' + e.message;
  }
}

async function swapModel(name) {
  const btns = document.querySelectorAll('.btn-swap');
  let btn = null;
  for (const b of btns) { if (b.textContent.trim() === 'load') { btn = b; break; } }
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> loading'; }
  try {
    await fetch(API + '/api/models/swap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name.trim()})
    });
    refreshModels();
  } catch(e) {
    if (btn) btn.innerHTML = 'load';
    alert('Swap failed: ' + e.message);
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escapeAttr(s) {
  return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
</script>
</body></html>"""


# ── HTTP Handler ──

class ArmorHandler(BaseHTTPRequestHandler):
    """HTTP handler for the ARMOR API + GUI."""

    def log_message(self, fmt, *args):
        pass  # silence default logging

    # ── Response helpers ──

    def _json(self, data: Any, status: int = 200):
        body = json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", MIME_JSON)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", MIME_STREAM)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _html(self):
        body = ARMOR_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", MIME_HTML)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length)) if length else {}

    # ── Routes ──

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/status":
            self._handle_status()
        elif path == "/api/models":
            self._handle_models()
        else:
            self._html()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/work":
            self._handle_work()
        elif path == "/api/chat":
            self._handle_chat()
        elif path == "/api/models/swap":
            self._handle_swap()
        else:
            self._json({"error": "not found"}, 404)

    # ── Handlers ──

    def _handle_status(self):
        r = _atlas("models", "loaded", timeout=10)
        loaded = r.stdout.strip() if r.returncode == 0 else None

        r2 = _atlas("models", "list", timeout=10)
        lines = r2.stdout.strip().split("\n") if r2.returncode == 0 else []

        self._json({
            "model": {"loaded": loaded or "(none)"},
            "models": lines,
        })

    def _handle_models(self):
        r = _atlas("models", "list", timeout=10)
        self._json({"output": r.stdout.strip()})

    def _handle_swap(self):
        body = self._body()
        name = body.get("name", "")
        r = _atlas("models", "swap", name, timeout=480)
        ok = r.returncode == 0
        self._json({"status": "ok" if ok else "error", "output": r.stdout.strip() or r.stderr.strip()})

    def _handle_work(self):
        self._stream()
        body = self._body()
        spec = body.get("spec", "")

        # Shell out to atlas work and relay events via stdout parsing.
        # For now, just run it and return the result as a single event.
        import time, os
        result = _atlas("work", spec, timeout=300, cwd=os.getcwd())
        if result.returncode == 0:
            self.wfile.write(stream_event("complete", status="completed", output=result.stdout.strip()))
        else:
            self.wfile.write(stream_event("error", message=result.stderr.strip() or "pipeline failed"))
        self.wfile.flush()

    def _handle_chat(self):
        self._stream()
        body = self._body()
        message = body.get("message", "")
        self.wfile.write(stream_event("done", content=f"[echo] {message}"))
        self.wfile.flush()


# ── CLI ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ARMOR — Atlas GUI layer")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="127.0.0.1", help="Bind address")
    args = parser.parse_args()

    server = HTTPServer((args.bind, args.port), ArmorHandler)
    print(f"ARMOR • http://{args.bind}:{args.port}")
    print(f"Engine • github.com/ColdSlither/atlas")
    print(f"Credit • Fork of pewdiepie-archdaemon/odysseus, inspired by pewdiepie")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
