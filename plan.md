# Implementation Plan: MCP Agent Hub

This plan outlines the steps required to build the MCP Agent Hub in a future session. Design decisions and the few open questions it depends on are tracked in `design-decisions.md`.

## Step 1: Project Initialization & Dependencies
1. Create a virtual environment (`python -m venv venv`) and activate it.
2. Install from the pinned `requirements.txt` (resolved: standalone **`fastmcp` 3.x** — see `design-decisions.md`, D13):
   `pip install -r requirements.txt` (`fastmcp>=3.4,<4`, `fastapi`, `uvicorn[standard]`, `aiosqlite`, `jinja2`, `pydantic`). Do **not** self-pin `starlette` — let `fastmcp` resolve it (3.4.1 floors `starlette>=1.0.1` for CVE-2026-48710).
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
1. `init_db()`: create the `agents` and `messages` tables if absent; set `PRAGMA journal_mode=WAL` (+ borrow `synchronous=NORMAL` / `busy_timeout` from MCP Agent Mail — see Prior Art); create indexes on `messages(recipient_id, status)` and `messages(session_id)`.
   * `agents`: `id` PK, `description` (TEXT, nullable), `skills` (TEXT/JSON), `status`, `last_seen`.
   * `messages`: `id` PK, `session_id`, `parent_id` (nullable), `kind` (`task`|`input_request`, default `task`), `sender_id`, `recipient_id`, `payload`, `context`, `response`, `status` (`pending`|`in_progress`|`input_required`|`completed`|`failed`|`expired`), `flagged_stale` (INT default 0), `claimed_at`, `created_at`, `updated_at`.
2. Connection helper: short-lived connections with `check_same_thread=False`, invoked off the event loop via `run_in_threadpool` (or use `aiosqlite`); a lightweight retry-on-`database is locked` with backoff (borrowed from Agent Mail).
3. Registry helpers: `upsert_agent(id, skills_json, description)`, `get_all_agents()`, `set_agent_offline(id)`, `touch_last_seen(id)`.
4. Message helpers:
   * `enqueue_message(...)` — mints/accepts `session_id`, sets optional `parent_id`/`kind`, sets `flagged_stale` when the recipient is stale (D6/Q8).
   * `claim_pending(agent_id)` — atomic `UPDATE ... SET status='in_progress', claimed_at=now ... RETURNING *` (no read-then-write race). Claims `pending` rows (incl. `kind='input_request'`) **and** stale `in_progress` rows; **excludes parked `input_required` rows**.
   * `request_input(message_id, question)` — park the task `→ input_required` and `enqueue_message` the child `input_request` back to the original sender (D17).
   * `complete_message(id, response)` — mark `completed`; **un-park rule:** if the row is an `input_request`, flip its `parent_id` task `input_required → pending`, clear its `claimed_at`, and append the answer to the parent's `context` (D17).
   * `fail_message(id, error)`, `get_status(id)` (surface the pending `question` when `input_required`).
   * `reclaim_stale(visibility_timeout)` — revert unacked `in_progress` rows to `pending` (never `input_required`).
   * `peek_inbox(agent_id)` — **read-only** count + sender summary of what `claim_pending` would return (backs `/api/peek`, D19); claims/mutates nothing.
   * `expire_messages(message_ttl)` — sweep `pending` rows older than `MESSAGE_TTL` to the terminal `expired` state (D6/Q3); never touches `in_progress` or parked `input_required`.
5. Store `skills` via `json.dumps`; parse with `json.loads` on read.

## Step 3: Web Dashboard (`templates/index.html` & `hub.py`)
1. In `hub.py`, initialize `app = FastAPI(lifespan=...)` (lifespan wired to the MCP app — see Step 4).
2. Configure Jinja2 templates.
3. Create the `/` route to serve `index.html`.
4. Create the JSON API routes: **`/api/state`** returning `{agents: get_all_agents(), messages: recent(limit=DASHBOARD_MESSAGE_LIMIT)}` (deriving online/stale/offline from `last_seen` + status); and the read-only **`/api/peek`** (`GET /api/peek?agent_id=…` → `db.peek_inbox()` → `{count, senders}`) backing the D19 hook layer — no claim, no mutation.
5. In `index.html`, fetch `/api/state` every 2 seconds and update the HTML tables. Use CDN Tailwind for styling, status badges (incl. **`Input Required`**, `Failed`, and **`Expired`**), a **⚠ stale-recipient** flag on `flagged_stale` messages, per-agent **skills** (name + tags, full detail on expand), `session_id` thread grouping, and a payload/response/question modal.

## Step 4: The FastMCP Server (`hub.py`)
1. Initialize `mcp = FastMCP("Agent Broker")`.
2. Add one cross-cutting middleware via `mcp.add_middleware(...)` whose `on_call_tool` hook refreshes `last_seen` (from the call's `agent_id` arg) and writes a structured event log row for the dashboard — so the 8 tool bodies stay focused (see `design-decisions.md`, D14).
3. Implement the `@mcp.tool()` decorators mapping to the `db.py` functions (**9 tools**):
   * `register_agent(agent_id, skills, description=None)` — structured Agent-Card `skills[]` (D16)
   * `list_agents()` — returns `{agent_id, description, skills, status, last_seen}`
   * `send_message(sender_id, recipient_id, payload, context=None, session_id=None)` → `{message_id, session_id}` — records `sender_id` (needed for `check_status` + D17 routing); validates the recipient (reject unknown / explicitly-disconnected; queue to known-but-stale **with `flagged_stale`**, D6/Q8)
   * `check_inbox()` — atomic claim + optional long-poll (`wait`/`timeout`); returns `session_id`/`parent_id`/`kind` per message
   * `reply_to_message()` — completes; **un-parks** the parent task when the replied row is an `input_request` (D17)
   * `fail_message()`
   * `request_input(message_id, question)` → `{request_message_id, session_id}` — park + ask (D17)
   * `check_status()` — surfaces the pending `question` when `input_required`
   * `disconnect_agent()`
4. Mount the MCP ASGI app at `/mcp` via `mcp_app = mcp.http_app(path="/mcp")`, then `app = FastAPI(lifespan=combine_lifespans(hub_lifespan, mcp_app.lifespan))` and `app.mount("/mcp", mcp_app)`. Forwarding the MCP lifespan is **mandatory** (else "task group is not initialized"). Bind to `127.0.0.1`, **and add `Origin`-header validation** (allow missing/localhost, reject foreign origins) as a small ASGI/Starlette middleware — the spec-mandated DNS-rebinding defense (D18).
5. Enforce the visibility timeout **lazily in the claim query** (the claim also grabs `in_progress` rows older than `VISIBILITY_TIMEOUT`); optionally start a small `asyncio` loop in `hub_lifespan` as a backstop that runs both `reclaim_stale(VISIBILITY_TIMEOUT)` and `expire_messages(MESSAGE_TTL)` (the `pending`→`expired` sweep, D6/Q3) — see `design-decisions.md`, D15. The read-only `/api/peek` route (Step 3) and the shipped `hook_peek.py` complete the D19 layer; `hook_peek.py` is stdlib-only (`urllib`) and adds no dependency.

## Step 5: Automated Tests (`tests/`)
1. DB unit tests:
   * atomic claim under concurrency (no double-delivery),
   * visibility-timeout redelivery of crashed/unacked messages,
   * `skills` JSON round-trip,
   * offline (explicit disconnect) vs stale (missed heartbeat) behavior,
   * **`input_required` round-trip** (D17): `request_input` parks the task + enqueues the child; the parked row is **excluded** from `claim_pending`/`reclaim_stale`; replying to the child **un-parks** the parent to `pending` with the answer in `context`,
   * **`flagged_stale`** set on a send to a stale recipient (D6/Q8),
   * **`expired` sweep** (D6/Q3): a `pending` row older than `MESSAGE_TTL` moves to `expired`; an `in_progress` or parked `input_required` row of the same age does **not**,
   * **`peek_inbox` is non-mutating** (D19): it reports the same count `claim_pending` would, and a following `claim_pending` still returns every message (peek claimed nothing).
2. A scripted MCP client smoke test: `register → send → check_inbox → request_input → (sender) check_inbox → reply → (worker) check_inbox → reply → check_status`. Use the MCP Inspector CLI against the running server, e.g. `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list` (and `--method tools/call --tool-name ...`), wired into the test flow.
3. An `Origin`-validation check: a request with a foreign `Origin` header is rejected; one with no `Origin` (or a localhost `Origin`) passes (D18).
4. A `/api/peek` HTTP check (D19): with N claimable messages for an agent, `GET /api/peek?agent_id=…` returns `count==N` + the sender list and mutates nothing (a subsequent `check_inbox` still claims all N).

## Step 6: End-to-End Testing
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
