# Setting Up the Hub Server

Get the hub running on your machine. Once it's up, wire your agents to it with
[connect-an-agent.md](connect-an-agent.md).

## Prerequisites

- **Python 3.10+** on PATH
- Windows, macOS, or Linux (the hub is pure Python; the one-click launcher is
  Windows-only)

## Quick start (Windows)

Double-click **`start_hub.bat`** (or run it from any terminal). It is portable and
self-healing:

- finds the project from its own location — no hardcoded paths;
- on first run (or with a missing/broken `venv/`) it creates the virtual environment
  and installs dependencies automatically, then launches;
- runs the supervisor and keeps the window open so errors stay visible. **Ctrl+C**
  stops it.

## Manual start (any OS)

```bash
python -m venv venv
source venv/bin/activate        # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_hub.py
```

`run_hub.py` is a small **supervisor**: it launches uvicorn
(`mcp_hub.hub:app` on `127.0.0.1:8000`) and relaunches it when the process exits with
code 42 — which is what the dashboard's *Restart* button triggers. Any other exit code
stops the supervisor.

## What you get once it's running

| Where | What |
|-------|------|
| `http://localhost:8000/` | the operator dashboard (live, SSE-pushed) |
| `http://localhost:8000/mcp` | the MCP endpoint agents connect to (Streamable HTTP) |
| `hub.db` (project root) | the SQLite database — all state lives here |
| `logs/hub.log` | server log (each supervisor launch appends a header) |

Sanity check: open the dashboard — an empty hub shows zero agents and an idle
activity feed. The first `register_agent` from any client appears there within a
second.

## Operating it

- **Restart (pick up code changes):** dashboard *Restart* button, or
  `POST /api/restart`. The supervisor relaunches the process; the SQLite DB is
  untouched and clients reconnect on their next call.
- **Soft reset (wipe state, keep running):** dashboard *Reset* button, or
  `POST /api/reset` — clears agents/messages. Irreversible.
- **Purge old data:** dashboard *Purge* button / `POST /api/purge` — removes old
  terminal-state rows.
- **Stop:** Ctrl+C in the supervisor window.
- **Logs:** `logs/hub.log`. The dashboard's activity feed shows the same story live.

> The `/api/*` endpoints validate the `Origin`/`Host` headers; call them from the
> dashboard or from localhost tooling, not from foreign origins.

## Tunables

All constants live at the top of `mcp_hub/db.py` and are single-sourced from there:
`STALE_THRESHOLD` (90 s), `VISIBILITY_TIMEOUT` (600 s), `MESSAGE_TTL` (24 h), and the
broadcast/offer caps. Edit + Restart to apply.

## Tests

```bash
pytest
```

Tests are non-destructive — they run on a temporary database, so it's safe to run
them while your live hub is serving agents.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `start_hub.bat` loops on venv creation | Is Python installed and on PATH? The window shows the failing step. |
| Port 8000 already in use | Stop the other process, or edit the port in `run_hub.py` (and everywhere clients point). |
| Dashboard shows nothing after a code edit | You edited but didn't restart — use the Restart button. |
| Client can't reach `/mcp` | Hub not running, or the client is not on the same machine — the hub is localhost-only by design (see the trust model in [how-it-works.md](how-it-works.md)). |
