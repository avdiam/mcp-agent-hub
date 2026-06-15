# CLAUDE.md

Guidance for Claude Code when working in the **MCP Agent Hub** project.

> **Override notice.** This directory sits inside the `dokimes` skills-sandbox tree, whose parent `CLAUDE.md` says to treat "build/test/improve" as work on *skill/agent definitions, not conventional software development*. That does **not** apply here. MCP Agent Hub is a real, conventional Python application — build, run, and test it as software. This file takes precedence for anything under `mcp-agent-hub/`.

## What this is

MCP Agent Hub is a **lightweight local message broker that lets independent CLI agents (e.g. Claude Code, Antigravity) talk to each other**. It runs one Python process exposing two interfaces:

1. An **MCP server** (Streamable HTTP, at `/mcp`) with tools for agents to register, discover peers, send messages, and check their inbox.
2. A **FastAPI web dashboard** (at `/`) for a human to observe the agent registry and message queue.

Messages are persisted in SQLite so work survives restarts.

## Current status: pre-implementation

There is **no code yet** — only design docs. They are the source of truth; read them before writing code:

- `project-purpose.md` — the problem and goals.
- `specs.md` — system components, the 9 MCP tools, dashboard, and storage schema.
- `architecture.md` — components, transport, and trust model.
- `plan.md` — the step-by-step build plan.
- `design-decisions.md` — the decision log (D1–D19), tunable constants, and any open questions. Check this before making design changes. *(As of 2026-06-15, all open questions Q1–Q9 are resolved.)*

## Session continuity — read these FIRST every session

This project **travels between two PCs** and deliberately uses **no local Claude memories** — nothing durable is stored in `~/.claude`. All preserved state lives in two checked-in files; read them at the start of every session and update them in the **same change** as the work they describe:

- `tasks.md` — **the source of truth for what's pending** (open questions, things to verify, the unstarted implementation steps).
- `sessions.md` — append-only **history of what's been done** (newest first). Add an entry before ending a session.

Do not rely on memory tools for this project; write it to these files instead.

## Intended layout (per `plan.md`)

```text
mcp-agent-hub/
├── hub.py            # FastAPI app + FastMCP server, mounted at /mcp
├── db.py             # all SQLite access (WAL mode)
├── hook_peek.py      # optional client hook: peeks /api/peek to nudge an agent (D19)
├── templates/
│   └── index.html    # dashboard (Jinja2 + Tailwind via CDN)
├── tests/            # db unit tests + scripted MCP smoke test
├── requirements.txt  # pinned deps
└── *.md              # the design docs above
```

## Tech stack

- Python 3.10+
- FastMCP — standalone `fastmcp` 3.x (`>=3.4,<4`), Streamable HTTP transport, **not** the deprecated HTTP+SSE transport (see `design-decisions.md`, D13)
- FastAPI + Uvicorn
- SQLite3 in WAL mode
- Jinja2 + Tailwind (CDN), vanilla JS polling `/api/state`

## Commands (once code exists)

```bash
# setup
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt

# run (binds localhost only)
uvicorn hub:app --port 8000 --host 127.0.0.1
# dashboard: http://localhost:8000   |   MCP endpoint: http://localhost:8000/mcp

# tests
pytest
```

## Conventions

- **Transport is Streamable HTTP** at `/mcp`. Do not reintroduce the legacy `/sse` + `/messages` transport.
- **All SQLite access goes through `db.py`**, runs in WAL mode, and stays off the event loop (`run_in_threadpool` or `aiosqlite`).
- **Delivery is at-least-once**: `check_inbox` claims atomically; an unacked `in_progress` message is redelivered after `VISIBILITY_TIMEOUT`. Handlers must tolerate a rare duplicate. A `pending` message unclaimed past `MESSAGE_TTL` is swept to a terminal `expired` state (D6/Q3).
- **The hook layer peeks, never claims (D19):** the optional `hook_peek.py` hits the read-only `/api/peek` endpoint only to *nudge* an agent to call `check_inbox`. Never let a hook mutate message state or open `hub.db` directly — delivery + ack stay in the MCP `check_inbox`→`reply`/`fail` path.
- **Store `skills` as JSON text** (the structured Agent-Card capability descriptor; SQLite has no array/object type).
- **Trust model:** single-user, localhost, no auth — bind `127.0.0.1` only.
- When changing the design, update the relevant doc(s) and the decision log in `design-decisions.md` in the same change.

## Git

This folder is a git repository (initialized 2026-06-15); `.gitignore` covers `venv/`, `__pycache__/`, and `hub.db`. Commit design/doc changes as you make them.
