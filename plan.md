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
   ├── templates/
   │   └── index.html   (Dashboard UI)
   ├── tests/           (db + MCP smoke tests)
   └── requirements.txt
   ```

## Step 2: Database Layer (`db.py`)
1. `init_db()`: create the `agents` and `messages` tables if absent; set `PRAGMA journal_mode=WAL`; create an index on `messages(recipient_id, status)`.
2. Connection helper: short-lived connections with `check_same_thread=False`, invoked off the event loop via `run_in_threadpool` (or use `aiosqlite`).
3. Registry helpers: `upsert_agent(id, capabilities_json)`, `get_all_agents()`, `set_agent_offline(id)`, `touch_last_seen(id)`.
4. Message helpers:
   * `enqueue_message(...)`
   * `claim_pending(agent_id)` — atomic `UPDATE ... SET status='in_progress', claimed_at=now ... RETURNING *` (no read-then-write race).
   * `complete_message(id, response)`, `fail_message(id, error)`, `get_status(id)`.
   * `reclaim_stale(visibility_timeout)` — revert unacked `in_progress` rows to `pending`.
5. Store `capabilities` via `json.dumps`; parse with `json.loads` on read.

## Step 3: Web Dashboard (`templates/index.html` & `hub.py`)
1. In `hub.py`, initialize `app = FastAPI(lifespan=...)` (lifespan wired to the MCP app — see Step 4).
2. Configure Jinja2 templates.
3. Create the `/` route to serve `index.html`.
4. Create an `/api/state` route returning `{agents: get_all_agents(), messages: recent(limit=DASHBOARD_MESSAGE_LIMIT)}`, deriving online/stale/offline from `last_seen` + status.
5. In `index.html`, fetch `/api/state` every 2 seconds and update the HTML tables. Use CDN Tailwind for styling, status badges (incl. `Failed`), and a payload/response modal.

## Step 4: The FastMCP Server (`hub.py`)
1. Initialize `mcp = FastMCP("Agent Broker")`.
2. Add one cross-cutting middleware via `mcp.add_middleware(...)` whose `on_call_tool` hook refreshes `last_seen` (from the call's `agent_id` arg) and writes a structured event log row for the dashboard — so the 8 tool bodies stay focused (see `design-decisions.md`, D14).
3. Implement the `@mcp.tool()` decorators mapping to the `db.py` functions (8 tools):
   * `register_agent()`
   * `list_agents()`
   * `send_message()` — validates the recipient (reject unknown / explicitly-disconnected; queue to known-but-stale)
   * `check_inbox()` — atomic claim + optional long-poll (`wait`/`timeout`)
   * `reply_to_message()`
   * `fail_message()`
   * `check_status()`
   * `disconnect_agent()`
4. Mount the MCP ASGI app at `/mcp` via `mcp_app = mcp.http_app(path="/mcp")`, then `app = FastAPI(lifespan=combine_lifespans(hub_lifespan, mcp_app.lifespan))` and `app.mount("/mcp", mcp_app)`. Forwarding the MCP lifespan is **mandatory** (else "task group is not initialized"). Bind to `127.0.0.1`.
5. Enforce the visibility timeout **lazily in the claim query** (the claim also grabs `in_progress` rows older than `VISIBILITY_TIMEOUT`); optionally start a small `asyncio` reclaim loop in `hub_lifespan` as a backstop (see `design-decisions.md`, D15).

## Step 5: Automated Tests (`tests/`)
1. DB unit tests:
   * atomic claim under concurrency (no double-delivery),
   * visibility-timeout redelivery of crashed/unacked messages,
   * `capabilities` JSON round-trip,
   * offline (explicit disconnect) vs stale (missed heartbeat) behavior.
2. A scripted MCP client smoke test: `register → send → check_inbox → reply → check_status`. Use the MCP Inspector CLI against the running server, e.g. `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list` (and `--method tools/call --tool-name ...`), wired into the test flow.

## Step 6: End-to-End Testing
1. Run the server: `uvicorn hub:app --port 8000 --host 127.0.0.1`.
2. Open the web dashboard at `http://localhost:8000`.
3. Configure Claude Code: `claude mcp add --transport http agent-hub http://localhost:8000/mcp` (or `.mcp.json` with `{"type": "http", "url": "http://localhost:8000/mcp"}`). Verify with `claude mcp list` and the `/mcp` panel (tool count).
4. Configure Antigravity CLI: add `{"mcpServers": {"agent-hub": {"serverUrl": "http://localhost:8000/mcp"}}}` to `~/.gemini/antigravity/mcp_config.json` (note the `serverUrl` key, not `url`). **Confirm the `serverUrl` → `localhost` connection actually succeeds here** — this is the one residual transport caveat (see `design-decisions.md`, D1/Q4).
5. In Claude Code, prompt: *"Register yourself, then ask Antigravity to write a haiku about APIs."*
6. Observe the web dashboard as the message populates.
7. In Antigravity CLI, prompt: *"Register, then check your inbox (wait for work) and respond to pending messages."*
8. Observe the response complete the loop on the dashboard, and confirm the sender sees it via `check_status`.
