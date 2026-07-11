# CLAUDE.md

Guidance for AGY when working in the **MCP Agent Hub** project.

> **Override notice.** This directory sits inside the `dokimes` skills-sandbox tree, whose parent `CLAUDE.md` says to treat "build/test/improve" as work on *skill/agent definitions, not conventional software development*. That does **not** apply here. MCP Agent Hub is a real, conventional Python application — build, run, and test it as software. This file takes precedence for anything under `mcp-agent-hub/`.

## What this is

MCP Agent Hub is a **lightweight local message broker that lets independent CLI agents (e.g. Claude Code, Antigravity) talk to each other**. It runs one Python process exposing two interfaces:

1. An **MCP server** (Streamable HTTP, at `/mcp`) with tools for agents to register, discover peers, send messages, and check their inbox.
2. A **FastAPI web dashboard** (at `/`) for a human to observe the agent registry and message queue.

Messages are persisted in SQLite so work survives restarts.

## Current status: v1 feature-complete (2026-06-18)

The application is implemented, running, and fully restructured into a package layout. Steps 1–6 of `docs/dev/plan.md` are done (E2E verified, cross-agent haiku exchange successful). The D19 peek/nudge hook layer is live on **both** clients. Security review and hardening (D18) is complete, preventing CSRF/DNS-rebinding via Origin/Host/Sec-Fetch-Site validation. Recovery controls (D26) are implemented via `/api/reset` and `/api/restart` mapped to a custom `run_hub.py` supervisor. The test suite is 12/12 green. v1 is successfully sealed.

**Post-v1 Workstreams:**
- **Workstream 1 (Stress-Test & Stabilize):** Complete. Heavy multi-agent load testing identified and resolved SQLite queue contention. `db.py` locks eliminated via WAL pragmas (`busy_timeout`/`synchronous`) and exponential backoff retry wrappers, yielding 100% success at 76+ ops/sec. Persistent connection pooling optimization explicitly deferred.

The design docs remain the source of truth for *why*; read them before changing behavior:

- `docs/dev/project-purpose.md` — the problem and goals.
- `docs/dev/specs.md` — system components, the 9 MCP tools, dashboard, and storage schema.
- `docs/dev/architecture.md` — components, transport, and trust model.
- `docs/dev/plan.md` — the step-by-step build plan.
- `docs/dev/design-decisions.md` — the decision log (D1–D27), tunable constants, and any open questions. Check this before making design changes. *(As of 2026-06-15, Q1–Q9 and the D20–D25 implementation-review decisions are all locked; D26–D27 added 2026-06-18.)*

## Session continuity — read these FIRST every session

This project is **worked on from two separate PCs**, so **nothing may be saved as a per-PC Claude memory.** Do **not** use Claude Code's built-in memory tool — it writes under `~/.claude/…` (its `MEMORY.md` + `memory/` files), which is local to one machine and will **not** travel between them. Anything worth preserving must live **inside the repo**, where it moves with `git`.

All preserved state lives in checked-in files; read them at the start of every session and update them in the **same change** as the work they describe:

- `docs/dev/tasks.md` — **the source of truth for what's pending** (open questions, things to verify, the unstarted implementation steps).
- `docs/dev/sessions.md` — append-only **history of what's been done** (newest first). Add an entry before ending a session.
- `docs/dev/mem/` — an **in-repo** folder of tracked markdown notes for anything durable that doesn't fit `tasks.md`/`sessions.md` (references, setup recipes, decisions-in-progress). The project-local stand-in for Claude memories — use it **instead of** `~/.claude`. (See `docs/dev/mem/README.md`.)

Do not rely on the built-in memory tools for this project — write to these files/folders (which travel with the repo) instead.

## Layout (actual)

```text
mcp-agent-hub/
├── README.md         # Explains project and installation
├── mcp_hub/          # Main package for MCP hub application
│   ├── hub.py        # FastAPI app + FastMCP server, mounted at /mcp
│   ├── db.py         # all SQLite access (WAL mode)
│   └── templates/    # dashboard HTML templates
├── docs/dev/         # Project tracking, plans, and durable notes
│   ├── mem/          # in-repo durable notes (travels with git)
│   └── *.md          # design docs, tasks, sessions
├── scripts/          # debug utilities and testing scripts
│   ├── prompts/      # Agent prompt templates
│   └── debug_*.py    # transport/initialize debugging helpers
├── tests/            # test_db.py + test_mcp.py
├── run_hub.py        # supervisor launcher
├── .claude/skills/agent-hub-live/  # live-messaging bundle: agent-hub-live skill + SETUP.md + scripts/hub_peek.py (peek-nudge hook, D19)
├── requirements.txt          # pinned deps
└── requirements-frozen.txt   # exact pins
```
*(`hub.db`, `logs/hub.log`, and other root `*.log` files are runtime artifacts and gitignored.)*

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

# run (binds localhost only, supports dashboard /api/restart)
python run_hub.py
# (Alternative: uvicorn mcp_hub.hub:app --port 8000 --host 127.0.0.1 for simple/dev run without auto-restart)
# dashboard: http://localhost:8000   |   MCP endpoint: http://localhost:8000/mcp

# tests — both suites are isolated (test_db.py: tmp_path; test_mcp.py: temp DB, no
# longer DELETEs the live hub.db). Run a single test:
pytest
pytest tests/test_mcp.py -k test_api_peek
```

## D19 hook layer (peek-nudge), per-client config

Both clients run the shared `hub_peek.py` (`.claude/skills/agent-hub-live/scripts/hub_peek.py`) to nudge themselves to call `check_inbox` when `/api/peek` reports pending messages. The script takes `--agent-id` (or `$AGENT_HUB_ID`) and a `--mode`: `prompt` prints a plain-text nudge (injected on `UserPromptSubmit`), `stop` emits a `{"decision":"block"}` JSON so a Claude `Stop` hook actually keeps the agent going (plain stdout is ignored on Stop). Claude Code wires it via **project** `.claude/settings.json` (`UserPromptSubmit` → `--mode prompt`, `Stop` → `--mode stop`); full guide in `.claude/skills/agent-hub-live/SETUP.md`. **Antigravity (agy CLI)** wires it via `~/.gemini/config/config.json` (`{"jsonHooksEnabled": true}`) + `~/.gemini/config/hooks.json`, with agy-specific requirements: the **nested** hook schema (a flat `{command,args}` silently loads **0 handlers**), a **single-string** command, and the **`--event-name`** flag — `PreInvocation` → `--mode prompt`, `Stop` → `--mode stop`. agy never EOFs the hook's stdin, so the notifier can't read the event name from it (and `hub_peek.py`'s stdin read is timeout-protected so the no-EOF stdin can't hang it). **Full verified config + caveats in README §3.** agy's ambient hooks are finicky (see AHB-7 in `agent-hub-issues.md`) — the active `/agent-hub-live` loop is the robust path on agy.

## Conventions

- **Transport is Streamable HTTP** at `/mcp`. Do not reintroduce the legacy `/sse` + `/messages` transport.
- **All SQLite access goes through `db.py`**, runs in WAL mode, and stays off the event loop via **`aiosqlite`** (D21 — so the long-poll is an async-poll, never a blocking threadpool hold). The DB tunables (`STALE_THRESHOLD`, `VISIBILITY_TIMEOUT`, `MESSAGE_TTL`) are **defined once in `db.py`** and imported by `hub.py` (D32/AHB-14) — change them there, not in `hub.py`.
- **Delivery is at-least-once**: `check_inbox` claims atomically; an unacked `in_progress` message is redelivered after `VISIBILITY_TIMEOUT`. Handlers must tolerate a rare duplicate. A `pending` **`task`** unclaimed past `MESSAGE_TTL` is swept to a terminal `expired` state (D6/Q3/D24). Completing a `task` fans a **`kind="result"`** message back to the sender's inbox (D20); **failing** one fans a **`kind="failure"`** the same way (D31/AHB-13) — both are ack-less (`NO_ACK_KINDS`) **internal deliveries** that bypass the offline/unknown recipient guard, so a departed sender doesn't drop them (D30/AHB-11). The `input_request` un-park is conditional (only while the parent is still `input_required`, D30/AHB-12); **failing** an `input_request` returns its parent to `pending` with the refusal noted, instead of stranding it (D31/AHB-13). `check_status` is the durable/secondary read.
- **Broadcast is one-to-many, ack-less, flood-capped (D33/AHB-1 P1):** the 10th tool `broadcast_message` (→ `db.broadcast`) fans a **`kind="announcement"`** to every non-offline agent **including the sender** (BD5), skipping explicitly-offline peers. Announcements are ack-less (`NO_ACK_KINDS = {result, failure, announcement}` — auto-complete on claim, never `reply`/`fail`). Per-sender flood caps (cooldown + hourly + payload/subject size + recipient ceiling) are enforced in `db.broadcast` against the durable `broadcasts` audit table; a violation delivers nothing and returns `{ok: false, error}`. **P1 reaches only the connected set** — durable announcements for late joiners are P2 (BD6). Unclaimed announcements are swept by the extended TTL sweep (D24/AHB-1).
- **The hook layer peeks, never claims (D19):** the optional `hub_peek.py` hits the `/api/peek` endpoint only to *nudge* an agent to call `check_inbox`. Peek **claims/mutates no message state** (it only refreshes the queried agent's own `last_seen` server-side — AHB-3/D29). Never let a hook mutate message state or open `hub.db` directly — delivery + ack stay in the MCP `check_inbox`→`reply`/`fail` path.
- **Store `skills` as JSON text** (the structured Agent-Card capability descriptor; SQLite has no array/object type).
- **Trust model:** single-user, localhost, no auth — bind `127.0.0.1` only. Hardened against cross-site attacks via strict Origin/Host/Sec-Fetch-Site validation (D18).
- When changing the design, update the relevant doc(s) and the decision log in `design-decisions.md` in the same change.

## Git

This folder is a git repository (initialized 2026-06-15); `.gitignore` covers `venv/`, `__pycache__/`, `hub.db`, and `logs/`. Commit design/doc changes as you make them.
