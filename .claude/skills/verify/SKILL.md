---
name: verify
description: How to launch and drive this repo's hub for end-to-end verification of a change — scratch instance, real MCP calls, SSE stream, dashboard in Chrome.
---

# Verifying MCP Agent Hub changes

The hub is a FastAPI + FastMCP server (`mcp_hub/hub.py`) with a browser dashboard at `/`,
REST under `/api/*`, and the MCP endpoint at `/mcp`. `hub.db` is **CWD-relative** (D27), so
verify against a scratch instance launched from a throwaway directory — never against the
repo root unless you intend to touch the real DB.

## Launch a scratch instance (port 8001, isolated DB)

```powershell
# from any scratch dir (its hub.db lands there):
& <repo>\venv\Scripts\python.exe -m uvicorn --app-dir <repo> mcp_hub.hub:app --host 127.0.0.1 --port 8001
```

Ready when `GET http://127.0.0.1:8001/api/state` returns 200. The venv is `venv\` at repo
root (not `.venv`). The real deployment uses `python run_hub.py` (supervisor, port 8000) —
check nothing is already on :8000 before assuming the field is clear.

## Drive the real surfaces

- **MCP tools** (through the middleware, real streamable-HTTP):
  ```python
  from fastmcp import Client
  async with Client('http://127.0.0.1:8001/mcp') as c:
      await c.call_tool('register_agent', {'agent_id': 'probe', 'skills': []})
  ```
- **SSE stream**: `curl -sN --max-time 30 http://127.0.0.1:8001/api/events` → expect one
  `data:` snapshot immediately, a new one after any mutation, `: keepalive` comments when
  idle (20 s).
- **REST mutations**: `POST /api/broadcast` (`{"payload": "..."}`) is the easiest
  state-change trigger; hostile-origin probes (`-H "Origin: http://evil.com"` or a bad
  `Host:`) must 403 on `/mcp` and `/api/*`.
- **Dashboard**: open `http://localhost:8001/` via claude-in-chrome; confirm panels update
  live (Refresh = "Live") with only two `/api/` requests in the network log (`/api/state`
  first paint + the `/api/events` stream). Mutate via MCP/REST and screenshot — panels must
  change without a reload.

## Gotchas

- httpx `ASGITransport` (used by `tests/test_mcp.py`) **buffers whole bodies** — it can
  never consume `/api/events`; the SSE tests use a real uvicorn on an ephemeral port
  (`live_server` fixture) for this reason. Don't "simplify" them back.
- Module-global asyncio primitives (`hub.notifier`) bind to the first event loop that uses
  them; tests get a fresh one per test via the autouse fixture.
- PowerShell backgrounds long `pytest`/`uvicorn` invocations; pipe through
  `Select-Object -Last N` shows nothing until the process exits — a silent run may be a
  hung SSE consumer, not a slow suite.
