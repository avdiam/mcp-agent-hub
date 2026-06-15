# MCP Agent Hub - Technical Specifications

## 1. System Components
The system consists of two interfaces served from a single Python application:
1. **MCP Server Interface (Streamable HTTP):** For local CLI agents to connect to. A single endpoint (`/mcp`) per the MCP `2025-03-26`+ transport revision.
2. **Web Dashboard (HTTP):** A browser-based UI for developers to monitor the hub.

> **Transport note:** The legacy HTTP+SSE transport (a `GET /sse` stream + a separate `POST /messages` endpoint) was deprecated in MCP spec revision `2025-03-26` and is **not** implemented here. We use Streamable HTTP, which most current clients and FastMCP support natively. Both target clients consume it: Claude Code via `"type": "http"` in `.mcp.json`, and Antigravity via the `serverUrl` key in `~/.gemini/antigravity/mcp_config.json`. See `design-decisions.md` (D1; verify Antigravity → `localhost` during E2E per plan Step 6).

## 2. Core Capabilities

### Agent Registration & Discovery
* Agents explicitly register with the Hub when starting a session or before communicating.
* The Hub maintains a registry of agents with their unique ID, stated capabilities, status, and `last_seen` timestamp.
* `last_seen` is refreshed on **every** tool call. An agent silent beyond `STALE_THRESHOLD` is rendered as "stale" (it may simply be busy or restarting — see liveness below).
* Agents can query the registry to find the correct recipient for a task.

### Reliable Message Brokering (at-least-once)
* Messages are routed via a SQLite-backed queue that survives server or agent restarts.
* State machine: `pending` -> `in_progress` -> `completed` / `failed`.
* **Atomic claim:** `check_inbox` claims an agent's `pending` messages in a single atomic statement (`UPDATE ... RETURNING`), flipping them to `in_progress` and stamping `claimed_at`. Concurrent callers never receive the same message twice.
* **Explicit ack:** `reply_to_message` moves a message to `completed`; `fail_message` moves it to `failed`. Either one acks the claim.
* **Visibility timeout / redelivery (lazy-on-claim):** an `in_progress` message not acked within `VISIBILITY_TIMEOUT` becomes eligible for redelivery — the atomic claim query grabs `pending` rows **and** `in_progress` rows whose `claimed_at` is older than the timeout, so a crashed/restarted worker's task is recovered the next time any consumer polls. No scheduler is required; an optional background loop is only a backstop for messages stranded while nobody polls (see `design-decisions.md`, D15). Delivery is therefore **at-least-once**, so message handlers should tolerate the occasional duplicate.
* Senders receive a `message_id` and poll for the response via `check_status`.

### Delivery / Notification
* Agents are CLI processes with no inbound port; the Hub **cannot push** to them.
* `check_inbox` supports a blocking long-poll (`wait=true`, `timeout=N`): the call returns as soon as a message is available, or when the timeout elapses. An "on-duty" agent stays parked in a single tool call instead of spin-polling or waiting for a human nudge.
* Recommended agent work-loop: `register_agent` → `check_inbox(wait=true)` → handle → `reply_to_message` → repeat.

### Disconnect / Liveness
* `disconnect_agent` moves an agent's status to `offline`. The Hub **rejects new sends** to an explicitly-disconnected agent.
* **Staleness is distinct from disconnect.** A stale agent (missed heartbeat) may just be busy or mid-restart, so messages addressed to it are still **queued** and redelivered when it returns. Only an explicit disconnect blocks sends.

## 3. MCP Tool Definitions

The FastMCP server exposes the following tools (8 total — note `check_status` and `fail_message`):

1. `register_agent(agent_id: str, capabilities: list[str]) -> str`
   * Registers the agent as online and refreshes `last_seen`.

2. `list_agents() -> list[dict]`
   * Returns currently registered agents and their `capabilities`, `status`, and `last_seen`.

3. `send_message(recipient_id: str, payload: str, context: str = None) -> str`
   * Submits a new message/task to the queue and returns a `message_id`.
   * **Validation:** rejects an unknown recipient or an explicitly-disconnected recipient; queues normally to a known-but-stale recipient.

4. `check_inbox(agent_id: str, wait: bool = False, timeout: int = 30) -> list[dict]`
   * Atomically claims this agent's `pending` messages → `in_progress`. With `wait=True`, blocks up to `timeout` seconds for the first message to arrive.

5. `reply_to_message(message_id: str, response: str) -> str`
   * Marks a message `completed` and attaches the response text (acks the claim).

6. `fail_message(message_id: str, error: str) -> str`
   * Marks a message `failed` with an error string (acks the claim). Completes the `failed` branch of the state machine.

7. `check_status(message_id: str) -> dict`
   * Lets the original sender check status and read the response/error.

8. `disconnect_agent(agent_id: str) -> str`
   * Marks the agent `offline` in the registry.

## 4. Web Dashboard Specifications

The dashboard is served at `http://localhost:8000/` (live data via `/api/state`; the MCP endpoint lives at `/mcp`). It features:
* **Header:** Status indicator of the Hub (Uptime, Total Messages).
* **Agents Panel:** A table listing all registered agents, their status (online / **stale** / offline, derived from `last_seen` + status), last-seen timestamp, and capabilities.
* **Message Queue Panel:** A live-updating table showing:
  * ID
  * Timestamp
  * Sender -> Recipient
  * Status Badge (Pending, In Progress, Completed, **Failed**)
  * A button to view the full payload/response in a modal.
* `/api/state` returns the recent messages (capped at `DASHBOARD_MESSAGE_LIMIT`) plus all agents, and is polled every 2 seconds.

## 5. Storage / Persistence
* Database: `sqlite3` in **WAL mode** (concurrent reads during writes; far fewer "database is locked" errors).
* File: `hub.db` stored in the same directory as the server script.
* Schema:
  * `agents` table: `id` (PK), `capabilities` (TEXT / JSON), `status`, `last_seen`.
  * `messages` table: `id` (PK), `sender_id`, `recipient_id`, `payload`, `context`, `response`, `status`, `claimed_at`, `created_at`, `updated_at`.
  * Index on `messages(recipient_id, status)` for inbox queries.
  * `capabilities` is serialized as JSON text (SQLite has no native array type).

## 6. Configuration Constants
Tunable values referenced above — `VISIBILITY_TIMEOUT`, `STALE_THRESHOLD`, the long-poll default `timeout`, and `DASHBOARD_MESSAGE_LIMIT` — with their proposed defaults are recorded in `design-decisions.md`.
