# ARMOR — The GUI layer for Atlas

> Inspired by [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) by PewDiePie.
> ARMOR is the Iron Man suit. [Atlas](https://github.com/ColdSlither/atlas) is the arc reactor.

A minimal, zero-dependency web GUI for the Atlas coding-agent harness. Runs alongside Atlas — it's a sibling, not a fork.

## Quickstart

```bash
# Install Atlas (the engine)
pip install -e ../atlas  # or: git clone https://github.com/ColdSlither/atlas.git

# Start ARMOR
python3 serve.py         # http://127.0.0.1:9090
```

## What it does

- **Work view** — type a task, watch the Editor→Verifier→Reviewer pipeline stream live via SSE (Server-Sent Events)
- **Chat view** — talk to Atlas through a browser (personality mode)
- **Models view** — see available models, swap between 8B and 35B tiers

## Zero dependencies

ARMOR uses Python's stdlib (`http.server`) + browser-native `EventSource`. No npm, no frameworks, no build step. One file, one port.

## Architecture

```
ARMOR (port 9090)          Atlas (port 8089)
┌──────────────────┐       ┌──────────────────┐
│  serve.py         │──────▶│  agent.py        │
│  HTTP API         │ HTTP   │  orchestrator.py │
│  + ARMOR HTML     │◀──────│  specialist.py   │
│                   │  JSON  │  models.py       │
└──────────────────┘  lines └──────────────────┘
```

ARMOR never imports Atlas directly — it shells out or calls the CLI. Atlas doesn't know ARMOR exists.

## Credits

ARMOR's architecture is inspired by [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus), PewDiePie's self-hosted AI workspace. The visual design (dark terminal theme, streaming output, model management panel) follows the same philosophy: minimal, local-first, dependency-light.
