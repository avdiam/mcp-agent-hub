# CLAUDE.md

Guidance for AGY when working in the **MCP Agent Hub** project.

> **Override notice.** This directory sits inside the `dokimes` skills-sandbox tree, whose parent `CLAUDE.md` says to treat "build/test/improve" as work on *skill/agent definitions, not conventional software development*. That does **not** apply here. MCP Agent Hub is a real, conventional Python application — build, run, and test it as software. This file takes precedence for anything under `mcp-agent-hub/`.

## What this is

MCP Agent Hub is a **lightweight local message broker that lets independent CLI agents (e.g. Claude Code, Antigravity) talk to each other**. It runs one Python process exposing two interfaces:

1. An **MCP server** (Streamable HTTP, at `/mcp`) with tools for agents to register, discover peers, send messages, and check their inbox.
2. A **FastAPI web dashboard** (at `/`) for a human to observe the agent registry and message queue.

Messages are persisted in SQLite so work survives restarts.

## Current status: v1 feature-complete (2026-06-18)

The application is implemented and running. Steps 1–5 of `plan.md` are done (`db.py`, `hub.py`, dashboard, tests). The FastMCP `initialize` -32602 bug is fixed (`ActivityTracker.__call__`). Step 6 E2E is verified: a full cross-agent haiku exchange ran through the hub (`claude-code-avdia` ↔ `antigravity-cli`), and the D19 peek/nudge hook layer is live on **both** clients (Claude Code `Stop`/`UserPromptSubmit`; agy `PreInvocationHook`/`StopHook`). Remaining before tagging v1: a green `pytest` (done — 10/10, after `test_mcp.py` was made non-destructive) and a `/security-review`.

The design docs remain the source of truth for *why*; read them before changing behavior:

- `project-purpose.md` — the problem and goals.
- `specs.md` — system components, the 9 MCP tools, dashboard, and storage schema.
- `architecture.md` — components, transport, and trust model.
- `plan.md` — the step-by-step build plan.
- `design-decisions.md` — the decision log (D1–D25), tunable constants, and any open questions. Check this before making design changes. *(As of 2026-06-15, Q1–Q9 and the D20–D25 implementation-review decisions are all locked.)*

## Session continuity — read these FIRST every session

This project is **worked on from two separate PCs**, so **nothing may be saved as a per-PC Claude memory.** Do **not** use Claude Code's built-in memory tool — it writes under `~/.claude/…` (its `MEMORY.md` + `memory/` files), which is local to one machine and will **not** travel between them. Anything worth preserving must live **inside the repo**, where it moves with `git`.

All preserved state lives in checked-in files; read them at the start of every session and update them in the **same change** as the work they describe:

- `tasks.md` — **the source of truth for what's pending** (open questions, things to verify, the unstarted implementation steps).
- `sessions.md` — append-only **history of what's been done** (newest first). Add an entry before ending a session.
- `mem/` — an **in-repo** folder of tracked markdown notes for anything durable that doesn't fit `tasks.md`/`sessions.md` (references, setup recipes, decisions-in-progress). The project-local stand-in for Claude memories — use it **instead of** `~/.claude`. (See `mem/README.md`.)

Do not rely on the built-in memory tools for this project — write to these files/folders (which travel with the repo) instead.

## Layout (actual)

```text
mcp-agent-hub/
├── hub.py            # FastAPI app + FastMCP server, mounted at /mcp
├── db.py             # all SQLite access (WAL mode)
├── hook_peek.py      # optional client hook: peeks /api/peek to nudge an agent (D19)
├── templates/
│   └── index.html    # dashboard (Jinja2 + Tailwind via CDN)
├── tests/            # test_db.py (tmp_path) + test_mcp.py (temp-DB, non-destructive)
├── requirements.txt          # pinned deps
├── requirements-frozen.txt   # exact pins from pip freeze (Step 1 residual, now done)
├── debug_mcp.py, debug_mcp_lowlevel.py, validate.py  # transport/initialize debugging helpers
├── mem/              # in-repo durable notes (travels with git)
└── *.md              # the design docs above
```
*(`hub.db`, `*.log` are runtime artifacts and gitignored.)*

## Tech stack

- Python 3.10+
- FastMCP — standalone `fastmcp` 3.x (`>=3.4,<4`), Streamable HTTP transport, **not** the deprecated HTTP+SSE transport (see `design-decisions.md`, D13)
- FastAPI + Uvicorn
- SQLite3 in WAL mode
- Jinja2 + Tailwind (CDN), vanilla JS polling `/api/state`

## Commands

```bash
# setup
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt

# run (binds localhost only)
uvicorn hub:app --port 8000 --host 127.0.0.1
# dashboard: http://localhost:8000   |   MCP endpoint: http://localhost:8000/mcp

# tests — both suites are isolated (test_db.py: tmp_path; test_mcp.py: temp DB, no
# longer DELETEs the live hub.db). Run a single test:
pytest
pytest tests/test_mcp.py -k test_api_peek
```

## D19 hook layer (peek-nudge), per-client config

Both clients run `hook_peek.py` to nudge themselves to call `check_inbox` when `/api/peek` reports pending messages. Claude Code wires it via `~/.claude/settings.json` (`Stop` + `UserPromptSubmit` → `python …/hook_peek.py --agent-id claude-code-avdia`). Antigravity wires it via `~/.gemini/config/`:

```json
// 1. ~/.gemini/config/config.json — enable json hooks:
{ "jsonHooksEnabled": true }

// 2. ~/.gemini/config/hooks.json — define the hooks:
{
  "PreInvocationHook": {
    "command": "python",
    "args": ["C:\\Users\\avdia\\Documents\\Projects\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"]
  },
  "StopHook": {
    "command": "python",
    "args": ["C:\\Users\\avdia\\Documents\\Projects\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"]
  }
}
```

## Conventions

- **Transport is Streamable HTTP** at `/mcp`. Do not reintroduce the legacy `/sse` + `/messages` transport.
- **All SQLite access goes through `db.py`**, runs in WAL mode, and stays off the event loop via **`aiosqlite`** (D21 — so the long-poll is an async-poll, never a blocking threadpool hold).
- **Delivery is at-least-once**: `check_inbox` claims atomically; an unacked `in_progress` message is redelivered after `VISIBILITY_TIMEOUT`. Handlers must tolerate a rare duplicate. A `pending` **`task`** unclaimed past `MESSAGE_TTL` is swept to a terminal `expired` state (D6/Q3/D24). Completing a `task` fans a **`kind="result"`** message back to the sender's inbox (D20); `check_status` is the durable/secondary read.
- **The hook layer peeks, never claims (D19):** the optional `hook_peek.py` hits the read-only `/api/peek` endpoint only to *nudge* an agent to call `check_inbox`. Never let a hook mutate message state or open `hub.db` directly — delivery + ack stay in the MCP `check_inbox`→`reply`/`fail` path.
- **Store `skills` as JSON text** (the structured Agent-Card capability descriptor; SQLite has no array/object type).
- **Trust model:** single-user, localhost, no auth — bind `127.0.0.1` only.
- When changing the design, update the relevant doc(s) and the decision log in `design-decisions.md` in the same change.

## Git

This folder is a git repository (initialized 2026-06-15); `.gitignore` covers `venv/`, `__pycache__/`, and `hub.db`. Commit design/doc changes as you make them.
