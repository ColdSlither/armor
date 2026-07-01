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

DEFAULT_PORT = 9090
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


# ── ARMOR HTML ──

ARMOR_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARMOR</title>
<style>
  :root {
    --bg: #0a0a0a; --fg: #c0c0c0;
    --green: #00ff41; --red: #ff3333; --yellow: #ffaa00;
    --blue: #00aaff; --dim: #555; --border: #222;
    --font: 'JetBrains Mono','Fira Code',monospace;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: var(--bg); color: var(--fg); font: 14px/1.5 var(--font);
         min-height: 100vh; display: flex; flex-direction: column; }
  header { border-bottom: 1px solid var(--border); padding: 0.75rem 1rem;
           display: flex; gap: 1rem; align-items: center; }
  header h1 { font-size: 16px; font-weight: normal; color: var(--green);
              margin: 0; letter-spacing: 1px; }
  header span { color: var(--dim); font-size: 12px; }
  nav { display: flex; gap: 0; border-bottom: 1px solid var(--border);
        padding: 0 1rem; }
  nav a { padding: 0.6rem 1rem; color: var(--dim); text-decoration: none;
          border-bottom: 2px solid transparent; transition: 0.15s; cursor: pointer; }
  nav a:hover { color: var(--fg); }
  nav a.active { color: var(--green); border-bottom-color: var(--green); }
  .page { flex: 1; display: none; padding: 1rem; overflow-y: auto; }
  .page.active { display: flex; flex-direction: column; }
  .input-row { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
  .input-row input { flex:1; background:#111; border:1px solid var(--border);
    color:var(--fg); font:14px var(--font); padding:0.6rem 0.8rem;
    outline:none; border-radius:4px; }
  .input-row input:focus { border-color:var(--green); }
  .input-row button { background:var(--green); color:#000; border:none;
    font:14px var(--font); padding:0.6rem 1.2rem; border-radius:4px;
    cursor:pointer; font-weight:bold; }
  .input-row button:disabled { opacity:0.4; cursor:not-allowed; }
  #work-output, #chat-output { flex:1; overflow-y:auto;
    background:#0d0d0d; border:1px solid var(--border); border-radius:4px;
    padding:0.8rem; font-size:13px; }
  .dispatch { padding: 0.3rem 0; display: flex; gap: 0.5rem; }
  .dispatch .round { color: var(--dim); }
  .dispatch.pass, .dispatch.approve { color: var(--green); }
  .dispatch.fail, .dispatch.request_changes { color: var(--yellow); }
  .dispatch.error { color: var(--red); }
  .dispatch.complete { color: var(--blue); border-top: 1px solid var(--border);
    margin-top: 0.5rem; padding-top: 0.5rem; }
  .chat-msg { margin-bottom: 0.8rem; }
  .chat-msg .role { font-weight: bold; margin-bottom: 0.2rem; }
  .chat-msg.user .role { color: var(--blue); }
  .chat-msg.assistant .role { color: var(--green); }
  .chat-msg .content { white-space: pre-wrap; }
  .chat-msg .content code { background: #111; padding: 0.1rem 0.3rem;
    border-radius: 3px; font-size: 13px; }
  #model-table { width:100%; border-collapse:collapse; }
  #model-table th, #model-table td { text-align:left; padding:0.5rem;
    border-bottom:1px solid var(--border); }
  #model-table th { color: var(--dim); font-weight: normal; }
  .model-status { display:inline-block; padding:0.15rem 0.5rem;
    border-radius:3px; font-size:12px; }
  .model-status.loaded { background:var(--green); color:#000; }
  .btn-swap { background:var(--blue); color:#000; border:none;
    font:12px var(--font); padding:0.2rem 0.6rem; border-radius:3px;
    cursor:pointer; }
  .btn-swap:disabled { opacity:0.3; cursor:not-allowed; }
</style>
</head>
<body>
<header>
  <h1>ARMOR</h1>
  <span>Atlas · 8B executor / 35B planner</span>
</header>
<nav>
  <a class="active" onclick="showPage('work')">Work</a>
  <a onclick="showPage('chat')">Chat</a>
  <a onclick="showPage('models')">Models</a>
</nav>

<div id="page-work" class="page active">
  <div class="input-row">
    <input id="work-input" placeholder="Describe the task..." autofocus>
    <button id="work-btn" onclick="runWork()">Go</button>
  </div>
  <div id="work-output"></div>
</div>

<div id="page-chat" class="page">
  <div id="chat-output"></div>
  <div class="input-row" style="margin-top:0.5rem;margin-bottom:0">
    <input id="chat-input" placeholder="Ask something...">
    <button id="chat-btn" onclick="sendChat()">Send</button>
  </div>
</div>

<div id="page-models" class="page">
  <div id="model-status" style="margin-bottom:1rem"></div>
  <table id="model-table"><thead><tr>
    <th>Name</th><th>Description</th><th>Size</th><th></th>
  </tr></thead><tbody></tbody></table>
</div>

<script>
const API = window.location.origin;

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelector('nav a[onclick*="' + name + '"]').classList.add('active');
  if (name === 'models') refreshModels();
  if (name === 'work') document.getElementById('work-input').focus();
  if (name === 'chat') document.getElementById('chat-input').focus();
}

// ── Work pipeline ──
async function runWork() {
  const input = document.getElementById('work-input');
  const btn = document.getElementById('work-btn');
  const spec = input.value.trim();
  if (!spec) return;
  const output = document.getElementById('work-output');
  output.innerHTML = '';
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
      const lines = buf.split('\n');
      buf = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const ev = JSON.parse(line);
          const div = document.createElement('div');
          if (ev.event === 'result') {
            let cls = 'dispatch ' + ev.verdict;
            div.className = cls;
            div.innerHTML = '<span class="round">→</span> ' + ev.specialist + ' (round ' + ev.round + ') <strong>' + ev.verdict.toUpperCase() + '</strong>';
            if (ev.findings && ev.findings.length < 80) {
              div.innerHTML += '<br><span style="color:var(--dim);font-size:12px">' + escapeHtml(ev.findings) + '</span>';
            }
          } else if (ev.event === 'complete') {
            div.className = 'dispatch complete';
            div.innerHTML = '<strong>' + ev.status.toUpperCase() + '</strong>' + (ev.commit_sha ? ' — commit ' + ev.commit_sha : '');
          } else if (ev.event === 'error') {
            div.className = 'dispatch error';
            div.innerHTML = '<strong>ERROR:</strong> ' + escapeHtml(ev.message || '');
          }
          if (div.innerHTML) output.appendChild(div);
        } catch(e) { /* skip partial line */ }
      }
    }
    output.scrollTop = output.scrollHeight;
  } catch(e) {
    output.innerHTML = '<div class="dispatch error">Error: ' + e.message + '</div>';
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
  const userDiv = document.createElement('div');
  userDiv.className = 'chat-msg user';
  userDiv.innerHTML = '<div class="role">You</div><div class="content">' + escapeHtml(msg) + '</div>';
  output.appendChild(userDiv);
  input.value = '';
  btn.disabled = true;
  const asstDiv = document.createElement('div');
  asstDiv.className = 'chat-msg assistant';
  asstDiv.innerHTML = '<div class="role">Atlas</div><div class="content"></div>';
  output.appendChild(asstDiv);
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
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const lines = buf.split('\n');
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
    contentDiv.textContent = 'Error: ' + e.message;
  }
  output.scrollTop = output.scrollHeight;
  btn.disabled = false;
}

// ── Models ──
async function refreshModels() {
  try {
    const resp = await fetch(API + '/api/models');
    const data = await resp.json();
    document.getElementById('model-status').innerHTML =
      'Loaded: <span class="model-status loaded">' + (data.loaded || 'none') + '</span>';
    const tbody = document.querySelector('#model-table tbody');
    tbody.innerHTML = '';
    for (const m of data.available) {
      const isLoaded = m.name === data.loaded;
      const tr = document.createElement('tr');
      tr.innerHTML = '<td>' + m.name + '</td><td>' + escapeHtml(m.description) + '</td><td>' + m.size_gb + ' GB</td>' +
        '<td>' + (isLoaded ? '<span class="model-status loaded">active</span>' :
          '<button class="btn-swap" onclick="swapModel(\'' + m.name + '\')">Swap</button>') + '</td>';
      tbody.appendChild(tr);
    }
  } catch(e) {
    document.getElementById('model-status').textContent = 'Error: ' + e.message;
  }
}

async function swapModel(name) {
  try {
    await fetch(API + '/api/models/swap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name})
    });
    refreshModels();
  } catch(e) {
    alert('Swap failed: ' + e.message);
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    print(f"Credit • Inspired by pewdiepie-archdaemon/odysseus")
    print("Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
