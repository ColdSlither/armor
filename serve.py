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
import os
import select
import subprocess
import sys
import threading
import time
import uuid
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Any


# ── Config ──

DEFAULT_PORT = 9092
# The Atlas CLI is a sibling checkout, not a fixed location: resolve it next to this repo so the
# path is not machine-specific, and let ATLAS_CLI override it for a different layout.
ATLAS_CLI = os.environ.get("ATLAS_CLI") or str(
    Path(__file__).resolve().parent.parent / "atlas-mac" / "agent.py"
)


# ── JSON helpers ──

def json_bytes(obj: Any) -> bytes:
    return (json.dumps(obj, default=str) + "\n").encode("utf-8")


def stream_event(event: str, **fields: Any) -> bytes:
    return json_bytes({"event": event, **fields})


# ── Atlas CLI wrapper ──

def _atlas(*args: str, timeout: int = 300, cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run an Atlas CLI command and return the result."""
    cmd = [sys.executable, str(ATLAS_CLI)] if Path(ATLAS_CLI).is_file() else [ATLAS_CLI]
    cmd.extend(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or str(Path.cwd()),
        env=env,
    )


def _atlas_stream(
    *args: str,
    timeout: int = 300,
    cwd: str | None = None,
    on_line: callable | None = None,
    proc_registry: dict | None = None,
    registry_key: str | None = None,
    cancel_check: callable | None = None,
) -> int:
    """Run an Atlas CLI command, streaming stdout line by line via ``on_line`` callback.

    Uses ``subprocess.Popen`` with line-buffered reading.  Each line (stripped of
    trailing newline) is passed to ``on_line``.  Returns the process returncode.
    Raises ``subprocess.TimeoutExpired`` on timeout.

    ``proc_registry`` (dict) + ``registry_key`` (str): if both given, the Popen
    handle is stored under ``registry_key`` while the process runs and removed
    on exit.  UHP cancellation uses this to kill a running pipeline.
    """
    cmd = [sys.executable, str(ATLAS_CLI)] if Path(ATLAS_CLI).is_file() else [ATLAS_CLI]
    cmd.extend(args)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Binary + unbuffered: the deadline loop below reads the OS fd directly.
        # Text mode (bufsize=1) would park complete lines in the TextIOWrapper
        # where select() cannot see them (false timeout kills healthy pipelines),
        # and readline() can block past the deadline on a partial line.
        text=False,
        bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        cwd=cwd or str(Path.cwd()),
    )
    if proc_registry is not None and registry_key is not None:
        proc_registry[registry_key] = proc
        # M2d: a cancel may have landed in the window between store and spawn.
        # Re-check immediately after registration so we don't run a pipeline
        # the client already cancelled.
        if cancel_check is not None and cancel_check():
            proc.kill()
    # Drain stderr on background thread to prevent deadlock
    stderr_lines: list[str] = []
    def _drain():
        for line in iter(proc.stderr.readline, ""):
            stderr_lines.append(line)
    threading.Thread(target=_drain, daemon=True).start()

    assert proc.stdout is not None, "stdout=PIPE guarantees a stream"
    stdout_fd = proc.stdout.fileno()

    def _kill_and_raise():
        proc.kill()
        proc.wait()
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    try:
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_and_raise()
            ready, _, _ = select.select([stdout_fd], [], [], remaining)
            if not ready:
                _kill_and_raise()
            chunk = os.read(stdout_fd, 65536)
            if not chunk:
                break
            buf.extend(chunk)
            # Emit every complete line in the chunk; keep the tail fragment
            # (it may complete on the next read).
            while True:
                nl = buf.find(b"\n")
                if nl == -1:
                    break
                line = bytes(buf[:nl]).decode("utf-8", "replace").rstrip("\r")
                del buf[: nl + 1]
                if on_line:
                    on_line(line)
            # A partial line can hold the fd open forever while a child stalls
            # mid-line; the deadline check at the top of the loop bounds that.
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        if proc_registry is not None and registry_key is not None:
            proc_registry.pop(registry_key, None)
    return proc.returncode


# ── MIME types ──

MIME_JSON = "application/json"
MIME_HTML = "text/html; charset=utf-8"
MIME_STREAM = "application/x-ndjson"
MIME_SSE = "text/event-stream"


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
  .ms.llama {
    background: rgba(0,229,255,0.08);
    color: var(--teal);
  }
  .ms.kt {
    background: rgba(255,140,0,0.10);
    color: var(--amber);
  }
  /* Chat model picker + markdown polish */
  .chat-toolbar {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    padding: 0.3rem 0;
    font-family: var(--font-ui);
    font-size: 11px;
    color: var(--fg-dim);
  }
  .chat-toolbar select {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    color: var(--fg);
    font: 11px var(--font-ui);
    padding: 0.2rem 0.4rem;
    border-radius: 3px;
    outline: none;
  }
  .chat-toolbar select:focus { border-color: var(--teal); }
  .chat-msg .content p { margin: 0.3rem 0; }
  .chat-msg .content ul, .chat-msg .content ol { margin: 0.3rem 0 0.3rem 1.2rem; }
  .chat-msg .content h1, .chat-msg .content h2, .chat-msg .content h3 {
    font-family: var(--font-ui);
    margin: 0.5rem 0 0.3rem;
    color: var(--teal);
  }
  .chat-msg .content blockquote {
    border-left: 2px solid var(--border);
    padding-left: 0.6rem;
    color: var(--fg-dim);
    margin: 0.3rem 0;
  }
  .msg-copy {
    float: right;
    background: none;
    border: 1px solid var(--border);
    color: var(--fg-dim);
    font: 9px var(--font-ui);
    padding: 0.1rem 0.4rem;
    border-radius: 3px;
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.15s;
  }
  .chat-msg:hover .msg-copy { opacity: 1; }
  .reasoning-block {
    font-size: 11px;
    color: var(--fg-dim);
    border-left: 2px solid var(--border-dim);
    padding-left: 0.5rem;
    margin: 0.2rem 0 0.4rem;
    max-height: 100px;
    overflow-y: auto;
    white-space: pre-wrap;
  }
  .reasoning-block.collapsed { max-height: 18px; overflow: hidden; cursor: pointer; }
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
  <div class="chat-toolbar">
    <span>model:</span>
    <select id="chat-model" onchange="loadChatModel()"></select>
    <span id="chat-model-status" style="color:var(--fg-dim)"></span>
  </div>
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
    <th>Model</th><th>Backend</th><th>Mode</th><th>Score</th><th>Size</th><th></th>
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
  if (name === 'chat') { document.getElementById('chat-input').focus(); refreshChatModelPicker(); }
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

// ── Work pipeline (SSE streaming) ──
function runWork() {
  const input = document.getElementById('work-input');
  const btn = document.getElementById('work-btn');
  const spec = input.value.trim();
  if (!spec) return;
  const output = document.getElementById('work-output');
  output.innerHTML = '';
  output.appendChild(buildPipelineBar());
  btn.disabled = true;

  const evtSource = new EventSource(API + '/api/work?spec=' + encodeURIComponent(spec));

  evtSource.onmessage = function(e) {
    let ev;
    try { ev = JSON.parse(e.data); } catch(_) { return; }
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
      evtSource.close();
      btn.disabled = false;
    } else if (ev.event === 'error') {
      div.className = 'dispatch error';
      div.innerHTML = '<strong>ERROR</strong> ' + escapeHtml(ev.message || '');
      evtSource.close();
      btn.disabled = false;
    } else if (ev.event === 'dispatch') {
      setStep(ev.specialist, 'active');
      return;
    } else {
      return;
    }
    if (div.innerHTML) output.appendChild(div);
    output.scrollTop = output.scrollHeight;
  };

  evtSource.onerror = function() {
    // Transport-level error — server closed the connection unexpectedly.
    // If the button is still disabled, re-enable it so the user can retry.
    if (btn.disabled) {
      btn.disabled = false;
    }
    evtSource.close();
  };
}

// ── Chat ──
// Conversation history lives in the browser; posted to Atlas each turn.
let chatHistory = [];

// Tiny markdown renderer (no deps). Heads, code, bold, lists, blockquote.
// Output is HTML; caller must have escapeHtml'd the input first via renderMd.
function renderMd(src) {
  // Escape first, then apply formatting (so model output can't inject HTML).
  let s = escapeHtml(src);
  // Code blocks ```...```
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
    '<pre><code>' + code.replace(/^\n/, '') + '</code></pre>');
  // Inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headers
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold + italic
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  // Blockquote
  s = s.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  // Unordered list
  s = s.replace(/^(\s*)[-*] (.+)$/gm, '<li>$2</li>');
  s = s.replace(/(<li>[\s\S]*?<\/li>)/g, m => '<ul>' + m + '</ul>');
  // Paragraphs (split on blank lines, skip block elements)
  s = s.split(/\n\n+/).map(para => {
    if (/^<(h\d|pre|ul|ol|blockquote|li)/.test(para.trim())) return para;
    return '<p>' + para.replace(/\n/g, '<br>') + '</p>';
  }).join('\n');
  return s;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('chat-btn');
  const msg = input.value.trim();
  if (!msg) return;
  const output = document.getElementById('chat-output');
  const modelSel = document.getElementById('chat-model');
  const targetModel = modelSel ? modelSel.value : '';

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
  const atBottom = output.scrollHeight - output.scrollTop - output.clientHeight < 60;
  if (atBottom) output.scrollTop = output.scrollHeight;

  const asstDiv = document.createElement('div');
  asstDiv.className = 'chat-msg assistant';
  asstDiv.style.display = 'none';
  asstDiv.innerHTML = '<div class="role">Atlas</div><div class="reasoning-block collapsed" style="display:none" onclick="this.classList.toggle(\'collapsed\')"></div><div class="content"></div>';
  const reasoningDiv = asstDiv.querySelector('.reasoning-block');
  const contentDiv = asstDiv.querySelector('.content');

  // Snapshot history before this turn; sent to Atlas, and we append after.
  const historyForAtlas = chatHistory.slice();

  try {
    const resp = await fetch(API + '/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, history: historyForAtlas, model: targetModel})
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let fullContent = '';
    let fullReasoning = '';
    let sawFirstToken = false;

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
          if (!sawFirstToken && (ev.event === 'chat_token' || ev.event === 'chat_done')) {
            sawFirstToken = true;
            thinkDiv.remove();
            asstDiv.style.display = '';
            output.appendChild(asstDiv);
          }
          if (ev.event === 'chat_token') {
            if (ev.kind === 'reasoning') {
              fullReasoning += ev.content;
              reasoningDiv.style.display = '';
              reasoningDiv.textContent = fullReasoning;
            } else {
              fullContent += ev.content;
              contentDiv.innerHTML = renderMd(fullContent);
            }
          } else if (ev.event === 'chat_done') {
            if (ev.content) { fullContent = ev.content; contentDiv.innerHTML = renderMd(fullContent); }
          } else if (ev.event === 'error') {
            if (!sawFirstToken) { thinkDiv.remove(); asstDiv.style.display = ''; output.appendChild(asstDiv); sawFirstToken = true; }
            contentDiv.innerHTML = '<span style="color:var(--red)">error: ' + escapeHtml(ev.message || '') + '</span>';
          }
          // Smart scroll: stick to bottom only if user was already there.
          const ab = output.scrollHeight - output.scrollTop - output.clientHeight < 60;
          if (ab) output.scrollTop = output.scrollHeight;
        } catch(e) {}
      }
    }
    // Add copy button to the finished message.
    if (fullContent) {
      const copyBtn = document.createElement('button');
      copyBtn.className = 'msg-copy';
      copyBtn.textContent = 'copy';
      copyBtn.onclick = () => { navigator.clipboard.writeText(fullContent); copyBtn.textContent = 'copied'; setTimeout(() => copyBtn.textContent = 'copy', 1200); };
      asstDiv.appendChild(copyBtn);
    }
    // Update history for the next turn.
    chatHistory.push({role: 'user', content: msg});
    chatHistory.push({role: 'assistant', content: fullContent});
  } catch(e) {
    thinkDiv.remove();
    asstDiv.style.display = '';
    output.appendChild(asstDiv);
    contentDiv.innerHTML = '<span style="color:var(--red)">error: ' + escapeHtml(e.message) + '</span>';
  }
  if (atBottom) output.scrollTop = output.scrollHeight;
  btn.disabled = false;
}

// Populate the chat model picker from /api/models (loadable configs only).
async function refreshChatModelPicker() {
  const sel = document.getElementById('chat-model');
  if (!sel) return;
  try {
    const resp = await fetch(API + '/api/models');
    const data = await resp.json();
    const loaded = data.loaded && data.loaded.loaded;
    const configs = data.configs || [];
    sel.innerHTML = '';
    for (const c of configs) {
      const opt = document.createElement('option');
      opt.value = c.name;
      opt.textContent = c.name + ' (' + c.backend + ')';
      if (loaded && c.name === loaded) opt.selected = true;
      sel.appendChild(opt);
    }
    const status = document.getElementById('chat-model-status');
    if (status) {
      status.textContent = loaded ? '→ ' + loaded + ' active' : '→ none loaded';
    }
  } catch(e) {}
}
function loadChatModel() {
  // Model selection is informational until Atlas supports per-chat --model
  // swapping without restarting the server. For now, show what's selected.
  const sel = document.getElementById('chat-model');
  const status = document.getElementById('chat-model-status');
  if (sel && status) status.textContent = '→ ' + sel.value + ' selected';
}

// ── Models ──
async function refreshModels() {
  try {
    const resp = await fetch(API + '/api/models');
    const data = await resp.json();
    const statusEl = document.getElementById('model-status');
    const tbody = document.querySelector('#model-table tbody');
    tbody.innerHTML = '';

    // Loaded state from structured JSON (no more prose regex).
    const loadedState = data.loaded || {};
    const loadedName = loadedState.loaded;
    const health = loadedState.health || 'down';
    const hw = (data.tier && data.tier.hardware) || null;

    let statusHtml = '';
    if (loadedName) {
      statusHtml = 'active: <span class="ms loaded">' + escapeHtml(loadedName) + '</span>';
    } else if (loadedState.source === 'port_probe') {
      statusHtml = '<span style="color:var(--amber)">server on port (unknown model)</span>';
    } else {
      statusHtml = '<span style="color:var(--fg-dim)">no model loaded</span>';
    }
    if (hw) {
      statusHtml += ' <span style="color:var(--fg-dim);font-size:10px">| ' +
        hw.vram_gb + 'GB VRAM, ' + (hw.kt_kernel ? 'kt-kernel ✓' : 'no kt-kernel') + '</span>';
    }
    statusEl.innerHTML = statusHtml;

    // Configured (loadable now) models — from data.configs.
    const configs = data.configs || [];
    // Tier-analyzed models — from data.tier.models (may include kt-class entries
    // that fit but can't be loaded yet because no ktransformers server exists).
    const tierModels = (data.tier && data.tier.available && data.tier.models) || [];

    // Render configured models first (these are the ones with working load buttons).
    for (const cfg of configs) {
      const isLoaded = loadedName && (cfg.name === loadedName || cfg.name === '8b' && loadedName === '8b');
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td><strong>' + escapeHtml(cfg.name) + '</strong>' +
          '<div style="color:var(--fg-dim);font-size:10px">' + escapeHtml(cfg.description || '') + '</div></td>' +
        '<td><span class="ms ' + (cfg.backend === 'ktransformers' ? 'kt' : 'llama') + '">' + escapeHtml(cfg.backend) + '</span></td>' +
        '<td style="color:var(--fg-dim)">configured</td>' +
        '<td style="color:var(--fg-dim)">—</td>' +
        '<td style="color:var(--fg-dim)">' + (cfg.size_gb ? cfg.size_gb + ' GB' : '-') + '</td>' +
        '<td>' + (isLoaded
          ? '<span class="ms loaded">active</span>'
          : '<button class="btn-swap" onclick="swapModel(\'' + escapeAttr(cfg.name) + '\')">load</button>') + '</td>';
      tbody.appendChild(tr);
    }

    // Then tier-analyzed models that AREN'T already in configs (the kt-class ones
    // — what fits on this hardware via offload but needs a server we don't have).
    const configNames = new Set(configs.map(c => c.name));
    for (const m of tierModels) {
      // Skip if already shown as a config, or if it doesn't fit.
      if (configNames.has(m.name) || !m.fits) {
        // Non-fitting models: show greyed with the reason.
        if (!m.fits) {
          const tr = document.createElement('tr');
          tr.style.opacity = '0.4';
          tr.innerHTML =
            '<td>' + escapeHtml(m.name) + '</td>' +
            '<td><span class="ms">—</span></td>' +
            '<td colspan="3" style="color:var(--red);font-size:11px">' + escapeHtml(m.reason || 'does not fit') + '</td>' +
            '<td></td>';
          tbody.appendChild(tr);
        }
        continue;
      }
      // Fits but kt-class (server not available) — read-only with blocked reason.
      const serverAvail = m.server_available !== false;
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + escapeHtml(m.name) +
          (m.is_moe ? ' <span style="color:var(--fg-dim);font-size:10px">MoE</span>' : '') + '</td>' +
        '<td><span class="ms ' + (m.backend === 'ktransformers' ? 'kt' : 'llama') + '">' + escapeHtml(m.backend) + '</span></td>' +
        '<td style="color:var(--teal);font-size:11px">' + escapeHtml(m.mode || '') + '</td>' +
        '<td>' + (m.score != null ? m.score.toFixed(0) : '—') + '</td>' +
        '<td style="color:var(--fg-dim)">' + (m.weights_gb ? m.weights_gb + ' GB' : '-') + '</td>' +
        '<td>' + (serverAvail
          ? '<button class="btn-swap" onclick="swapModel(\'' + escapeAttr(m.name) + '\')">load</button>'
          : '<span title="' + escapeAttr(m.load_blocked_reason || '') + '" style="color:var(--amber);font-size:10px;cursor:help">blocked</span>') + '</td>';
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


# ── UHP surface (bones) ──
# Structural contract of the Unified Harness Protocol (2026-09-12):
#   POST /v1/responses        — run a task (stream or single object)
#   GET  /v1/responses/{id}   — read a stored response
#   POST /v1/responses/{id}/cancel — cancel a running task
#   GET  /v1/harnesses        — discovery
# Session continuation (previous_response_id) is deliberately REJECTED (501)
# until atlas work can take prior context — see M4. Same for Idempotency-Key.
# Not conformance-complete by design — bones only: the wire shapes, the
# event vocabulary, sequence numbers, and session semantics. The task surface
# is OpenAI Responses-compatible so existing clients can drive Atlas.

UHP_VERSION = "2026-09-12"
UHP_HARNESS_ID = "atlas"
UHP_TIERS = {"8b", "7bs", "4bs", "14b", "35b", "auto"}

# In-memory response store (store: true). Lost on restart — bones.
UHP_RESPONSES: dict[str, dict] = {}
UHP_PROCS: dict[str, Any] = {}  # response_id -> Popen, for cancellation
UHP_LOCK = threading.Lock()  # guards store mutations + terminal transitions
_UHP_MAX_BODY = 1_048_576  # 1 MiB request cap (M3: unbounded read = memory DoS)


def _uhp_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _uhp_response(
    rid: str,
    status: str,
    model: str,
    metadata: dict,
    output: list | None = None,
    error: dict | None = None,
    incomplete_details: dict | None = None,
    previous_response_id: str | None = None,
) -> dict:
    return {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "previous_response_id": previous_response_id,
        "model": model,
        "output": output or [],
        "store": True,
        "usage": None,  # never fabricate a zero we can't account for
        "metadata": metadata,
    }


def _uhp_item(iid: str, **fields: Any) -> dict:
    return {"id": iid, "status": "completed", **fields}


class UHPHandlerMixin:
    """UHP route handlers, mixed into ArmorHandler."""

    def _handle_v1_harnesses(self):
        self._json({
            "object": "list",
            "data": [{
                "id": UHP_HARNESS_ID,
                "object": "harness",
                "name": "Atlas",
                "description": "Editor → Verifier → Reviewer pipeline over the Atlas CLI",
                "models": sorted(UHP_TIERS),
            }],
        })

    def _handle_v1_responses_post(self):
        body = self._body()
        if body is False:
            return  # 413 already replied by _body
        if body is None:
            self._json({"error": {"code": "invalid_input", "message": "request body must be a JSON object"}}, 400)
            return
        spec = body.get("input")
        if not spec or not isinstance(spec, str):
            self._json({"error": {"code": "invalid_input", "message": "input must be a non-empty string"}}, 400)
            return

        # Harness selection (Tasks §1.2)
        meta_in = body.get("metadata") or {}
        if not isinstance(meta_in, dict):
            self._json({"error": {"code": "invalid_input", "message": "metadata must be an object"}}, 400)
            return
        harness_id = meta_in.get("harness_id") or UHP_HARNESS_ID
        if harness_id != UHP_HARNESS_ID:
            self._json({"error": {"code": "harness_not_found", "message": f"unknown harness '{harness_id}'"}}, 404)
            return

        # Session continuation (Sessions §1) — REJECTED loudly (M4 decision).
        # Copying session_id without passing prior context to atlas is a silent
        # fake: the wire says "continued session", the behavior is a fresh
        # pipeline. Until atlas work supports a context/history input, refuse
        # rather than let clients build on fiction.
        previous = body.get("previous_response_id")
        if previous:
            self._json({"error": {"code": "not_supported",
                                  "message": "session continuation (previous_response_id) is not supported: atlas work has no context input; re-submit the full task in input instead"}}, 501)
            return

        # Model selection (Tasks §1.3): tier names are our canonical ids.
        model = body.get("model")
        if model is not None and model not in UHP_TIERS:
            self._json({"error": {"code": "model_unavailable", "message": f"tier '{model}' unknown; use one of {sorted(UHP_TIERS)}"}}, 422)
            return

        # Budgets (Tasks §1.1)
        max_step = body.get("max_step")
        timeout_s = body.get("timeout_seconds") or 300
        try:
            timeout_s = int(timeout_s)
        except (TypeError, ValueError):
            timeout_s = 300

        # Reserved / unsupported fields — MUST be named, never acted on (Tasks §1.4)
        ignored = [f for f in ("tools", "include", "instructions", "background", "max_output_tokens") if f in body]

        session_id = _uhp_id("hsess")  # fresh session; continuation rejected above (M4)
        rid = _uhp_id("resp")
        resp = _uhp_response(
            rid, "in_progress", model or "auto",
            {"session_id": session_id, "harness_id": UHP_HARNESS_ID, "ignored_fields": ignored},
            previous_response_id=previous,
        )
        UHP_RESPONSES[rid] = resp

        cmd = ["work", spec]
        if model:
            cmd += ["--tier", model]
        if max_step is not None:
            try:
                cmd += ["--max-rounds", str(int(max_step))]
            except (TypeError, ValueError):
                pass

        stream = bool(body.get("stream"))
        if not stream:
            # Non-streaming: assemble the same output the stream would (Streaming §6)
            output = []
            pipeline_status = [None]  # parsed from Atlas's "[Pipeline: X]" line
            try:
                rc = _atlas_stream(*cmd, timeout=timeout_s, cwd=os.getcwd(), proc_registry=UHP_PROCS, registry_key=rid,
                                   cancel_check=lambda: resp.get("status") == "cancelled",
                                   on_line=lambda ln: self._uhp_collect(ln, output, pipeline_status))
            except subprocess.TimeoutExpired:
                with UHP_LOCK:
                    if resp["status"] == "in_progress":
                        resp["status"] = "incomplete"
                        resp["incomplete_details"] = {"reason": "timeout", "timeout_seconds": timeout_s}
                    resp["output"] = output
            except Exception as exc:
                with UHP_LOCK:
                    if resp["status"] == "in_progress":
                        resp["status"] = "failed"
                        resp["error"] = {"code": "pipeline_failed", "message": str(exc)[:300]}
                    resp["output"] = output
            else:
                with UHP_LOCK:
                    resp["output"] = output
                    # M2b: never clobber a status the cancel handler already
                    # stamped (spec: cancelled MUST end "cancelled", never failed).
                    if resp["status"] == "in_progress":
                        # The "[Pipeline: X]" summary line is authoritative for the
                        # pipeline outcome; the returncode alone lies (exits 0 even
                        # on exhausted rounds). Prefer the parsed status.
                        parsed = pipeline_status[0]
                        if parsed == "completed":
                            resp["status"] = "completed"
                        elif parsed in ("failed", "incomplete"):
                            resp["status"] = parsed
                            if parsed == "failed":
                                resp["error"] = {"code": "pipeline_failed", "message": "atlas reported failed"}
                            else:
                                resp["incomplete_details"] = {"reason": "pipeline_exhausted"}
                        elif rc == 0:
                            resp["status"] = "completed"
                        elif rc < 0:
                            resp["status"] = "cancelled"
                            resp["error"] = {"code": "cancelled", "message": "cancelled by client"}
                        else:
                            resp["status"] = "failed"
                            resp["error"] = {"code": "pipeline_failed", "message": f"atlas exited {rc}"}
            self._json(resp)
            return

        # Streaming (Streaming §1): SSE with strict sequence numbers.
        self._sse()
        seq = [0]
        client_gone = [False]

        def emit(event_type: str, **payload: Any) -> None:
            if client_gone[0]:
                return
            data = {"type": event_type, "sequence_number": seq[0], **payload}
            seq[0] += 1
            try:
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Client disconnected — stop writing but keep finalizing the
                # stored response so GET /v1/responses/{id} stays truthful.
                client_gone[0] = True

        emit("response.created", response=resp)
        emit("response.in_progress", response=resp)

        output: list[dict] = []
        pipeline_status: list[str | None] = [None]  # parsed from "[Pipeline: X]" — resolved under lock
        pipeline_error: list[dict | None] = [None]  # from the (defensive) error NDJSON branch

        def on_line(line: str) -> None:
            line = line.strip()
            if not line:
                return
            # Terminal prose line from Atlas: "[Pipeline: completed]" etc.
            if line.startswith("[Pipeline:"):
                status = line[len("[Pipeline:"):].rstrip("]").strip().lower()
                pipeline_status[0] = status if status in ("completed", "incomplete", "failed") else None
                return
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                return
            ev = data.get("event")
            if ev == "dispatch":
                item = _uhp_item(_uhp_id("fc"),
                                 type="function_call", call_id=f"call_{data.get('round', 1)}",
                                 name=data.get("specialist"), arguments="{}", status="in_progress")
                output.append(item)
                emit("response.output_item.added", output_index=len(output) - 1, item=item)
            elif ev == "result":
                if not output:
                    return
                item = output[-1]
                args = json.dumps({"verdict": data.get("verdict"), "findings": data.get("findings", "")})
                item["arguments"] = args
                item["status"] = "completed"
                emit("response.function_call_arguments.done", output_index=len(output) - 1, item_id=item["id"], arguments=args)
                emit("response.output_item.done", output_index=len(output) - 1, item=item)
                fco = _uhp_item(_uhp_id("fco"), type="function_call_output",
                                call_id=item["call_id"], output=str(data.get("verdict", "")))
                output.append(fco)
                emit("response.output_item.added", output_index=len(output) - 1, item=fco)
                emit("response.output_item.done", output_index=len(output) - 1, item=fco)
            elif ev == "error":
                msg = data.get("message", "pipeline error")
                pipeline_status[0] = "failed"
                pipeline_error[0] = {"code": "pipeline_failed", "message": msg[:300]}
                emit("error", code="pipeline_failed", message=msg[:300], param=None)

        try:
            rc = _atlas_stream(*cmd, timeout=timeout_s, cwd=os.getcwd(), proc_registry=UHP_PROCS, registry_key=rid,
                               cancel_check=lambda: resp.get("status") == "cancelled", on_line=on_line)
        except subprocess.TimeoutExpired:
            with UHP_LOCK:
                if resp["status"] == "in_progress":
                    resp["status"] = "incomplete"
                    resp["incomplete_details"] = {"reason": "timeout", "timeout_seconds": timeout_s}
                resp["output"] = output
        except Exception as exc:
            with UHP_LOCK:
                if resp["status"] == "in_progress":
                    resp["status"] = "failed"
                    resp["error"] = {"code": "pipeline_failed", "message": str(exc)[:300]}
                resp["output"] = output
        else:
            with UHP_LOCK:
                resp["output"] = output
                # M2b: never clobber a status the cancel handler stamped.
                if resp["status"] == "in_progress":
                    parsed = pipeline_status[0]
                    if parsed == "completed":
                        resp["status"] = "completed"
                    elif parsed == "incomplete":
                        resp["status"] = "incomplete"
                        resp["incomplete_details"] = {"reason": "pipeline_exhausted"}
                    elif parsed == "failed":
                        resp["status"] = "failed"
                        resp["error"] = pipeline_error[0] or {"code": "pipeline_failed", "message": "atlas reported failed"}
                    elif rc == 0:
                        resp["status"] = "completed"
                    elif rc < 0:
                        resp["status"] = "cancelled"
                        resp["error"] = {"code": "cancelled", "message": "cancelled by client"}
                    else:
                        resp["status"] = "failed"
                        resp["error"] = {"code": "pipeline_failed", "message": f"atlas exited {rc}"}

        # M2c + m3: status->event map is explicit — a cancelled task terminates
        # with response.failed carrying status "cancelled" (never completed),
        # and we never emit a terminal event for a non-terminal status.
        terminal = {"completed": "response.completed", "failed": "response.failed",
                    "incomplete": "response.incomplete", "cancelled": "response.failed"}.get(resp["status"])
        if terminal is None:
            with UHP_LOCK:
                if resp["status"] == "in_progress":
                    resp["status"] = "completed"  # rc==0, no [Pipeline:] line seen
            terminal = "response.completed"
        emit(terminal, response=resp)
        # The stream ends with the terminal event — close the connection so
        # clients see EOF and curl -N returns instead of hanging on keep-alive.
        self.close_connection = True

    def _uhp_collect(self, line: str, output: list, pipeline_status: list | None = None) -> None:
        """Non-streaming collector: same item assembly as the stream path."""
        line = line.strip()
        if not line:
            return
        if line.startswith("[Pipeline:"):
            # Authoritative pipeline outcome — same source the stream path uses.
            if pipeline_status is not None:
                status = line[len("[Pipeline:"):].rstrip("]").strip().lower()
                if status in ("completed", "incomplete", "failed"):
                    pipeline_status[0] = status
            return
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        ev = data.get("event")
        if ev == "dispatch":
            output.append(_uhp_item(_uhp_id("fc"), type="function_call",
                                    call_id=f"call_{data.get('round', 1)}",
                                    name=data.get("specialist"), arguments="{}"))
        elif ev == "result":
            if not output:
                return
            item = output[-1]
            item["arguments"] = json.dumps({"verdict": data.get("verdict"), "findings": data.get("findings", "")})
            output.append(_uhp_item(_uhp_id("fco"), type="function_call_output",
                                    call_id=item["call_id"], output=str(data.get("verdict", ""))))

    def _handle_v1_response_get(self, rid: str):
        resp = UHP_RESPONSES.get(rid)
        if resp is None:
            self._json({"error": {"code": "response_not_found", "message": f"no stored response '{rid}'"}}, 404)
            return
        self._json(resp)

    def _handle_v1_response_cancel(self, rid: str):
        with UHP_LOCK:
            resp = UHP_RESPONSES.get(rid)
            if resp is None:
                self._json({"error": {"code": "response_not_found", "message": f"no stored response '{rid}'"}}, 404)
                return
            # M2a: cancelling an already-terminal task MUST succeed and change
            # nothing (Sessions §4) — never rewrite history.
            if resp["status"] in ("completed", "failed", "incomplete", "cancelled"):
                self._json(resp)
                return
            # Stamp cancelled FIRST (UHP: status field is authoritative) so a
            # concurrent finalize in the stream thread sees it, then kill.
            resp["status"] = "cancelled"
            resp["error"] = {"code": "cancelled", "message": "cancelled by client"}
        # M2d: the Popen may not be registered yet (store happens before spawn).
        # Give it a short window to appear so the kill actually lands; if it
        # never appears, _atlas_stream's cancel_check re-check aborts on spawn.
        proc = UHP_PROCS.get(rid)
        if proc is None:
            for _ in range(10):
                time.sleep(0.05)
                proc = UHP_PROCS.get(rid)
                if proc is not None:
                    break
        if proc is not None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._json(resp)


# ── HTTP Handler ──

class ArmorHandler(UHPHandlerMixin, BaseHTTPRequestHandler):
    """HTTP handler for the ARMOR API + GUI + UHP surface."""

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

    def _sse(self, status: int = 200):
        """Send SSE (Server-Sent Events) response headers."""
        self.send_response(status)
        self.send_header("Content-Type", MIME_SSE)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _html(self):
        body = ARMOR_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", MIME_HTML)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any] | None:
        """Parse the request body defensively. Returns None on malformed input
        (the caller replies 400) instead of crashing the handler thread — a
        crash yields zero bytes of HTTP response and a client-side
        RemoteDisconnected (GLM-5.3 M3)."""
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError:
            return None
        if length < 0:
            return None
        if length > _UHP_MAX_BODY:
            # Reply 413 upstream; signal via sentinel rather than an exception.
            self._json({"error": {"code": "body_too_large", "message": f"body exceeds {_UHP_MAX_BODY} bytes"}}, 413)
            return False
        if length == 0:
            return {}
        try:
            raw = self.rfile.read(length)
        except OSError:
            return None
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        return body

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
        elif path == "/api/work":
            self._handle_work()
        elif path == "/v1/harnesses":
            self._handle_v1_harnesses()
        elif path.startswith("/v1/responses/"):
            rid = path[len("/v1/responses/"):]
            if not rid or "/" in rid:
                self._json({"error": {"code": "response_not_found", "message": "malformed response id"}}, 404)
                return
            self._handle_v1_response_get(rid)
        else:
            self._html()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/chat":
            self._handle_chat()
        elif path == "/api/models/swap":
            self._handle_swap()
        elif path == "/v1/responses":
            self._handle_v1_responses_post()
        elif path.startswith("/v1/responses/") and path.endswith("/cancel"):
            rid = path[len("/v1/responses/"):-len("/cancel")]
            if not rid or "/" in rid:
                self._json({"error": {"code": "response_not_found", "message": "malformed response id"}}, 404)
                return
            self._handle_v1_response_cancel(rid)
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
        """Structured model list. Calls `atlas models list --json --tier-select`
        and passes the structured blob through directly — no regex parsing.

        If the tier selector env var isn't set, degrades to configs-only JSON.
        Atlas handles the selector-availability check and returns tier.available=False.
        """
        tier_selector = os.environ.get("ATLAS_TIER_SELECTOR_PATH", "")
        args = ["models", "list", "--json"]
        if tier_selector:
            args.append("--tier-select")
        env = {**os.environ, "ATLAS_TIER_SELECTOR_PATH": tier_selector} if tier_selector else None
        r = _atlas(*args, timeout=30, env=env)
        if r.returncode == 0 and r.stdout.strip():
            try:
                self._json(json.loads(r.stdout.strip()))
                return
            except json.JSONDecodeError:
                pass  # fall through to error
        self._json({"configs": [], "tier": {"available": False, "reason": "atlas models --json failed"},
                    "loaded": {}, "error": r.stderr.strip()[:200] or r.stdout.strip()[:200]})

    def _handle_work(self):
        """Stream pipeline results via SSE (Server-Sent Events)."""
        self._sse()

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        spec = params.get("spec", [""])[0]

        if not spec:
            msg = json.dumps({"event": "error", "message": "missing spec query param"})
            self.wfile.write(f"event: message\ndata: {msg}\n\n".encode())
            self.wfile.flush()
            return

        def on_line(line: str):
            """Relay NDJSON events from atlas stdout as SSE."""
            line = line.strip()
            if not line:
                return
            try:
                data = json.loads(line)
                if "event" in data:
                    self.wfile.write(b"event: message\n")
                    self.wfile.write(f"data: {line}\n\n".encode())
                    self.wfile.flush()
            except json.JSONDecodeError:
                pass  # not NDJSON

        try:
            _atlas_stream("work", spec, timeout=300, cwd=os.getcwd(), on_line=on_line)
        except subprocess.TimeoutExpired:
            msg = json.dumps({"event": "error", "message": "pipeline timed out"})
            self.wfile.write(f"event: message\ndata: {msg}\n\n".encode())
            self.wfile.flush()

    def _handle_chat(self):
        """Real chat: stream atlas chat --message --json NDJSON to the browser.

        Contract:
          POST body: {"message": str, "history": [{role, content}, ...]}
          Response: application/x-ndjson stream of events:
            {"event": "chat_token", "kind": "content"|"reasoning", "content": str}
            {"event": "chat_done",  "content": str}     # full assembled answer
            {"event": "error",      "message": str}     # on failure

        History is written to a PID-named temp file (cleaned in finally) and
        passed via --history so each request is stateless on Atlas's side —
        ARMOR owns the conversation transcript in the browser.
        """
        self._stream()
        body = self._body()
        message = body.get("message", "")
        history = body.get("history", [])

        if not message:
            self.wfile.write(stream_event("error", message="empty message"))
            self.wfile.flush()
            return

        # Write history to a temp file. One file per request, cleaned in finally.
        tmp_path = Path(f"/tmp/armor-chat-{os.getpid()}-{id(message)}.json")
        try:
            tmp_path.write_text(json.dumps(history))
            cmd_args = ["chat", "--message", message, "--history", str(tmp_path), "--json"]

            def on_line(line: str):
                line = line.strip()
                if not line:
                    return
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    return  # not NDJSON (e.g. stderr noise that leaked through)
                # Pass through Atlas's chat events verbatim. Atlas emits:
                #   chat_token (kind=content|reasoning), chat_done.
                # ARMOR's frontend already handles these shapes.
                self.wfile.write((json.dumps(data) + "\n").encode("utf-8"))
                self.wfile.flush()

            try:
                _atlas_stream(*cmd_args, timeout=180, on_line=on_line)
            except subprocess.TimeoutExpired:
                self.wfile.write(stream_event("error", message="chat timed out"))
                self.wfile.flush()
            except Exception as exc:
                self.wfile.write(stream_event("error", message=f"chat failed: {exc}"))
                self.wfile.flush()
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


# ── CLI ──

def main():
    import argparse
    global ATLAS_CLI
    parser = argparse.ArgumentParser(description="ARMOR — Atlas GUI layer")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="127.0.0.1", help="Bind address")
    parser.add_argument("--atlas", default=ATLAS_CLI,
                        help="Path to the Atlas CLI (agent.py) or 'atlas' if on PATH. "
                             "e.g. ../atlas/agent.py")
    parser.add_argument("--tier-selector", default=os.environ.get("ATLAS_TIER_SELECTOR_PATH", ""),
                        help="Path to atlas_tier_selection.py (enables tier-aware Models view). "
                             "e.g. ../intron-terminal-pipeline/atlas_tier_selection.py")
    args = parser.parse_args()

    ATLAS_CLI = args.atlas
    if args.tier_selector:
        os.environ["ATLAS_TIER_SELECTOR_PATH"] = args.tier_selector

    # ThreadingHTTPServer so /v1/responses/{id}/cancel can reach a running
    # pipeline stream on another connection (single-threaded would deadlock).
    server = ThreadingHTTPServer((args.bind, args.port), ArmorHandler)
    print(f"ARMOR • http://{args.bind}:{args.port}")
    print(f"Engine • {ATLAS_CLI}")
    print(f"Credit • Fork of pewdiepie-archdaemon/odysseus, inspired by pewdiepie")
    if args.tier_selector:
        print(f"Tier • {args.tier_selector}")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
