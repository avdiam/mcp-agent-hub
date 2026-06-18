# Implementation Plan: MCP Agent Hub

This plan outlines the steps required to build the MCP Agent Hub in a future session. Design decisions and the few open questions it depends on are tracked in `design-decisions.md`.

## Build phasing (D25)

Build as a **walking skeleton**, proving the riskiest unknown — two *different* CLI agents completing a real round-trip over Streamable HTTP + long-poll — before enriching. The Steps below are the *what*; these phases are the *order*. The full schema columns (`sender_id`, `session_id`, `parent_id`, `kind`, `flagged_stale`, full status enum) ship in **P1** so later phases add behaviour, not migrations.

- **P1 — skeleton + green E2E:** the ~7 core tools (`register_agent`, `list_agents`, `send_message`, `check_inbox` long-poll, `reply_to_message`, `fail_message`, `check_status`), minimal `db.py` (full schema), minimal dashboard → run the **cross-client haiku E2E** (Step 6.1–6.8, requester reads the result via `check_status`).
- **P2 — collaboration:** D16 structured `skills[]` (Pydantic `Skill`) + D17 `input_required`/`request_input` + **D20 result-to-inbox delivery**.
- **P3 — hygiene & safety:** D6 `expired` sweep + `flagged_stale`, D18 `Origin` validation.
- **P4 — push-feel:** D19 hook peek/nudge layer + the nudge E2E (Step 6.9–6.10).

Run **`/code-review`** on each phase's diff, **`/security-review`** after P3, and use **`/run` + `/verify`** for the E2E.

## Step 1: Project Initialization & Dependencies
1. Create a virtual environment (`python -m venv venv`) and activate it.
2. Install from the pinned `requirements.txt` (resolved: standalone **`fastmcp` 3.x** — see `design-decisions.md`, D13):
   `pip install -r requirements.txt` (`fastmcp>=3.4,<4`, `fastapi`, `uvicorn[standard]`, `aiosqlite`, `jinja2`, `pydantic`; dev: `pytest`, `httpx`, `ruff`). Do **not** self-pin `starlette` — let `fastmcp` resolve it (3.4.1 floors `starlette>=1.0.1` for CVE-2026-48710). **`pip freeze` after install** to lock patch versions. **Use Context7** to re-verify the live FastMCP 3.x API (`http_app`, `combine_lifespans`, `add_middleware`/`on_call_tool`, the in-memory `Client`) + current FastAPI/uvicorn before coding against them (these facts postdate the assistant's training cutoff); optionally scaffold the FastMCP server with the **`mcp-server-dev`** plugin.
3. Create the basic directory structure:
   ```text
   mcp-agent-hub/
   ├── hub.py           (Main FastAPI/FastMCP server)
   ├── db.py            (SQLite interactions)
   ├── hook_peek.py     (stdlib-only client hook: peeks /api/peek, nudges the agent — D19)
   ├── templates/
   │   └── index.html   (Dashboard UI)
   ├── tests/           (db + MCP smoke tests)
   └── requirements.txt
   ```

## Step 2: Database Layer (`db.py`)
1. `init_db()`: create the `agents` and `messages` tables if absent; set `PRAGMA journal_mode=WAL` (+ borrow `synchronous=NORMAL` / `busy_timeout` from MCP Agent Mail — see Prior Art); create indexes on `messages(recipient_id, status)`, `messages(session_id)`, and `messages(created_at)`; **assert `sqlite3.sqlite_version_info >= (3, 35)`** (the atomic `UPDATE…RETURNING` claim — D4 — needs it; fail loudly if older).
   * `agents`: `id` PK, `description` (TEXT, nullable), `skills` (TEXT/JSON), `status`, `last_seen`.
   * `messages`: `id` PK, `session_id`, `parent_id` (nullable), `kind` (`task`|`input_request`|`result`, default `task`), `sender_id`, `recipient_id`, `payload`, `context`, `response`, `status` (`pending`|`in_progress`|`input_required`|`completed`|`failed`|`expired`), `flagged_stale` (INT default 0), `claimed_at`, `created_at`, `updated_at`.
2. Connection helper: short-lived connections invoked off the event loop via **`aiosqlite`** (the chosen path — D21, so the long-poll never blocks a threadpool worker); a lightweight retry-on-`database is locked` with backoff (borrowed from Agent Mail).
3. Registry helpers: `upsert_agent(id, skills_json, description)`, `get_all_agents()`, `set_agent_offline(id)`, `touch_last_seen(id)`.
4. Message helpers:
   * `enqueue_message(...)` — mints/accepts `session_id`, sets optional `parent_id`/`kind`, sets `flagged_stale` when the recipient is stale (D6/Q8).
   * `claim_pending(agent_id)` — atomic `UPDATE ... SET status='in_progress', claimed_at=now ... RETURNING *` (no read-then-write race). Claims `pending` rows (incl. `kind='input_request'`) **and** stale `in_progress` rows; **excludes parked `input_required` rows**; a `kind='result'` row is returned **and** set `completed` in the same statement (delivered, no ack — D20).
   * `request_input(message_id, question)` — park the task `→ input_required` and `enqueue_message` the child `input_request` back to the original sender (D17).
   * `complete_message(id, response)` — mark `completed`; **result fan-out:** if the row is a `task`, `enqueue_message` a `kind='result'` (carrying `response`) to its `sender_id`, threaded by `session_id`/`parent_id` (D20); **un-park rule:** if the row is an `input_request`, flip its `parent_id` task `input_required → pending`, clear its `claimed_at`, and append the answer to the parent's `context` (D17).
   * `fail_message(id, error)`, `get_status(id)` (surface the pending `question` when `input_required`).
   * `reclaim_stale(visibility_timeout)` — revert unacked `in_progress` rows to `pending` (never `input_required`).
   * `peek_inbox(agent_id)` — **read-only** count + sender summary of what `claim_pending` would return (incl. `result` notifications; backs `/api/peek`, D19); claims/mutates nothing.
   * `expire_messages(message_ttl)` — sweep `pending` rows of **`kind='task'`** older than `MESSAGE_TTL` to the terminal `expired` state (D6/Q3/D24); never touches `in_progress`, the `input_request`/`result` derived messages, or parked `input_required`.
5. Store `skills` via `json.dumps`; parse with `json.loads` on read.

## Step 3: Web Dashboard (`templates/index.html` & `hub.py`)
1. In `hub.py`, initialize `app = FastAPI(lifespan=...)` (lifespan wired to the MCP app — see Step 4).
2. Configure Jinja2 templates.
3. Create the `/` route to serve `index.html`.
4. Create the JSON API routes: **`/api/state`** returning `{agents: get_all_agents(), messages: recent(limit=DASHBOARD_MESSAGE_LIMIT), events: activity_buffer(), stats: {uptime, total_messages}}` (deriving online/stale/offline from `last_seen` + status; `events` is the in-memory Activity ring buffer — D22); and the read-only **`/api/peek`** (`GET /api/peek?agent_id=…` → `db.peek_inbox()` → `{count, senders}`) backing the D19 hook layer — no claim, no mutation.
5. Generate `index.html` with the **`frontend-design`** skill (CDN Tailwind + vanilla JS, no build step). Fetch `/api/state` every 2 seconds and update the agents table, the message queue, and an **Activity panel** (recent tool calls — D22). Status badges (incl. **`Input Required`**, `Failed`, **`Expired`**), a `kind` indicator (task / **input_request** / **result**), a **⚠ stale-recipient** flag on `flagged_stale` messages, per-agent **skills** (name + tags, full detail on expand), `session_id` thread grouping, and a payload/response/question modal.

## Step 4: The FastMCP Server (`hub.py`)
1. Initialize `mcp = FastMCP("Agent Broker")`.
2. Add one cross-cutting middleware via `mcp.add_middleware(...)` whose `on_call_tool` hook refreshes `last_seen` (from the direct actor arg where present — `agent_id`/`sender_id`, D23) and appends a per-call event to an in-memory ring buffer for the dashboard's Activity panel (D22) — so the 9 tool bodies stay focused (see `design-decisions.md`, D14).
3. Implement the `@mcp.tool()` decorators mapping to the `db.py` functions (**9 tools**). Write each tool's **docstring as a first-class deliverable** — it becomes the MCP tool description the agents actually read (the work-loop; how to answer an `input_request`; that results arrive as `kind='result'` inbox messages). This is the real agent UX (goal #4).
   * `register_agent(agent_id, skills, description=None)` — structured Agent-Card `skills[]` via a Pydantic `Skill` model (D16)
   * `list_agents()` — returns `{agent_id, description, skills, status, last_seen}`
   * `send_message(sender_id, recipient_id, payload, context=None, session_id=None)` → `{message_id, session_id}` — records `sender_id` (needed for `check_status`, D17 routing, and the D20 result fan-out); validates the recipient (reject unknown / explicitly-disconnected; queue to known-but-stale **with `flagged_stale`**, D6/Q8)
   * `check_inbox(agent_id, wait=True, timeout=30)` — atomic claim + long-poll (**`wait=True` default**, async-poll every `LONGPOLL_INTERVAL` — D21); returns `session_id`/`parent_id`/`kind` per message; a `result` row is auto-completed on claim (D20)
   * `reply_to_message()` — completes; **result fan-out** to the sender's inbox when the row is a `task` (D20); **un-parks** the parent task when the row is an `input_request` (D17)
   * `fail_message()`
   * `request_input(message_id, question)` → `{request_message_id, session_id}` — park + ask (D17)
   * `check_status()` — surfaces the pending `question` when `input_required` (durable/secondary read; the result is also pushed to the sender's inbox — D20)
   * `disconnect_agent()`
4. Mount the MCP ASGI app at `/mcp` via `mcp_app = mcp.http_app(path="/mcp")`, then `app = FastAPI(lifespan=combine_lifespans(hub_lifespan, mcp_app.lifespan))` and `app.mount("/mcp", mcp_app)`. Forwarding the MCP lifespan is **mandatory** (else "task group is not initialized"). Bind to `127.0.0.1`, **and add `Origin`-header validation** (allow missing/localhost, reject foreign origins) as a small ASGI/Starlette middleware — the spec-mandated DNS-rebinding defense (D18).
5. Enforce the visibility timeout **lazily in the claim query** (the claim also grabs `in_progress` rows older than `VISIBILITY_TIMEOUT`); optionally start a small `asyncio` loop in `hub_lifespan` as a backstop that runs both `reclaim_stale(VISIBILITY_TIMEOUT)` and `expire_messages(MESSAGE_TTL)` (the `pending kind='task'`→`expired` sweep, D6/Q3/D24) — see `design-decisions.md`, D15. The read-only `/api/peek` route (Step 3) and the shipped `hook_peek.py` complete the D19 layer; `hook_peek.py` is stdlib-only (`urllib`) and adds no dependency.

## Step 5: Automated Tests (`tests/`)
1. DB unit tests:
   * atomic claim under concurrency (no double-delivery),
   * visibility-timeout redelivery of crashed/unacked messages,
   * `skills` JSON round-trip,
   * offline (explicit disconnect) vs stale (missed heartbeat) behavior,
   * **`input_required` round-trip** (D17): `request_input` parks the task + enqueues the child; the parked row is **excluded** from `claim_pending`/`reclaim_stale`; replying to the child **un-parks** the parent to `pending` with the answer in `context`,
   * **`flagged_stale`** set on a send to a stale recipient (D6/Q8),
   * **`expired` sweep** (D6/Q3): a `pending` row older than `MESSAGE_TTL` moves to `expired`; an `in_progress` or parked `input_required` row of the same age does **not**,
   * **`peek_inbox` is non-mutating** (D19): it reports the same count `claim_pending` would, and a following `claim_pending` still returns every message (peek claimed nothing),
   * **result fan-out** (D20): completing a `task` enqueues a `kind='result'` to the sender; claiming it returns it **and** marks it `completed` in the same step (no separate ack, not redelivered by the visibility timeout); the response is also on the task row for `check_status`.
2. A scripted MCP smoke test of the tool-logic round-trip (`register → send → check_inbox → request_input → (sender) check_inbox → reply → (worker) check_inbox → reply → check_status`, plus the **D20 result landing in the sender's inbox**) using the **in-memory `fastmcp.Client(mcp)`** (pure-Python, CI-friendly, no Node). Keep **one** real-over-HTTP check via the MCP Inspector CLI — `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list` (and `--method tools/call --tool-name ...`) — to exercise the actual Streamable HTTP wire.
3. An `Origin`-validation check: a request with a foreign `Origin` header is rejected; one with no `Origin` (or a localhost `Origin`) passes (D18).
4. A `/api/peek` HTTP check (D19): with N claimable messages for an agent, `GET /api/peek?agent_id=…` returns `count==N` + the sender list and mutates nothing (a subsequent `check_inbox` still claims all N).
5. Run **`/code-review`** on each phase's diff (especially after the Step-4 core) before committing; the in-memory `Client` tests + the one HTTP check run under `pytest`.

## Step 6: End-to-End Testing

Drive this with **`/run`** (launch the hub + open the dashboard) and **`/verify`** (confirm the real behaviour, not just green tests). Steps 1–8 are the **P1** haiku E2E; 9–10 are the **P4** hook layer.

1. Run the server: `uvicorn hub:app --port 8000 --host 127.0.0.1`.
2. Open the web dashboard at `http://localhost:8000`.
3. Configure Claude Code: `claude mcp add --transport http agent-hub http://localhost:8000/mcp` (or `.mcp.json` with `{"type": "http", "url": "http://localhost:8000/mcp"}`). Verify with `claude mcp list` and the `/mcp` panel (tool count).
4. Configure Antigravity CLI: add `{"mcpServers": {"agent-hub": {"serverUrl": "http://localhost:8000/mcp"}}}` to **`~/.gemini/config/mcp_config.json`** — the path the `agy` CLI actually reads (Windows `C:\Users\<you>\.gemini\config\mcp_config.json`), **not** `~/.gemini/antigravity/mcp_config.json`. Note the `serverUrl` key (not `url`); write it **UTF-8 without BOM** (the Go JSON parser rejects a BOM) and don't leave the file empty (an empty file logs `unexpected end of JSON input`). **Verified 2026-06-15** that AGY connects to a `http://localhost` Streamable HTTP endpoint this way — D1/Q4 caveat closed (see `sessions.md`).
5. In Claude Code, prompt: *"Register yourself, then ask Antigravity to write a haiku about APIs."*
6. Observe the web dashboard as the message populates.
7. In Antigravity CLI, prompt: *"Register, then check your inbox (wait for work) and respond to pending messages."*
8. Observe the response complete the loop on the dashboard, and confirm the sender sees it via `check_status`.
9. **Wire the D19 hook layer.** Install `hook_peek.py` into both clients: Claude Code via `~/.claude/settings.json` (`Stop` / `UserPromptSubmit` running `python hook_peek.py --agent-id <id>`); Antigravity `agy` via `hooks.json` (`StopHook` / `PreInvocationHook`, with `json-hooks-enabled`). Confirm each peeks `GET /api/peek` on its trigger.
10. **Confirm the nudge path:** send a message to an agent that is between turns, then fire its hook (finish a turn / submit a prompt) and confirm the injected "you have N messages — call `check_inbox`" nudge appears and the agent then claims via `check_inbox`. Note the honest limit — a fully idle agent waiting on a human won't see it until its next trigger.
