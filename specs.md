# MCP Agent Hub - Technical Specifications

## 1. System Components
The system consists of two interfaces served from a single Python application:
1. **MCP Server Interface (Streamable HTTP):** For local CLI agents to connect to. A single endpoint (`/mcp`) per the MCP `2025-03-26`+ transport revision.
2. **Web Dashboard (HTTP):** A browser-based UI for developers to monitor the hub.

> **Transport note:** The legacy HTTP+SSE transport (a `GET /sse` stream + a separate `POST /messages` endpoint) was deprecated in MCP spec revision `2025-03-26` and is **not** implemented here. We use Streamable HTTP, which most current clients and FastMCP support natively. Both target clients consume it: Claude Code via `"type": "http"` in `.mcp.json`, and Antigravity (the `agy` CLI) via the `serverUrl` key in `~/.gemini/config/mcp_config.json` (**verified live 2026-06-15** — the AGY CLI completed a full MCP handshake against a `http://localhost` Streamable-HTTP endpoint this way). See `design-decisions.md` (D1/Q4).
>
> **Origin validation (D18):** in addition to binding `127.0.0.1`, the server validates the HTTP `Origin` header on `/mcp` requests — the MCP spec mandates this as a DNS-rebinding defense. Requests with no `Origin` (non-browser CLI clients) pass; requests bearing a non-localhost `Origin` are rejected.

## 2. Core Capabilities

### Agent Registration & Discovery
* Agents explicitly register with the Hub when starting a session or before communicating.
* The Hub maintains a registry of agents with their unique ID, an optional one-line `description`, a structured list of **`skills`**, status, and `last_seen` timestamp.
* **Structured skills (Agent-Card-style — `design-decisions.md` D16):** each skill is `{ id, name, description, tags[], examples[] }`, borrowed from the A2A `AgentSkill`. The `tags` and free-text `description`/`examples` let `list_agents` answer "which agent can do X?" instead of dumping an opaque string list. (`id`/`name`/`description` required per skill; `tags`/`examples` optional.)
* `last_seen` is refreshed on every tool call that carries a direct actor arg (`agent_id`/`sender_id` — D23). An agent silent beyond `STALE_THRESHOLD` is rendered as "stale" (it may simply be busy or restarting — see liveness below).
* Agents can query the registry (`list_agents`) to find the correct recipient for a task.

### Reliable Message Brokering (at-least-once)
* Messages are routed via a SQLite-backed queue that survives server or agent restarts.
* State machine: `pending` → `in_progress` → `completed` / `failed`, plus a non-terminal **`input_required`** branch — a worker can pause `in_progress` work to ask the original sender a clarifying question (see *Multi-turn Clarification & Sessions* below; `design-decisions.md` D17). A `pending` `task` never claimed within `MESSAGE_TTL` is swept to the terminal **`expired`** state (`design-decisions.md` D6/Q3/D24).
* **Atomic claim:** `check_inbox` claims an agent's `pending` messages in a single atomic statement (`UPDATE ... RETURNING`), flipping them to `in_progress` and stamping `claimed_at`. Concurrent callers never receive the same message twice. (A delivered `kind="result"` notification is marked `completed` in the same claim — it needs no ack, D20.)
* **Explicit ack:** `reply_to_message` moves a message to `completed`; `fail_message` moves it to `failed`. Either one acks the claim.
* **Visibility timeout / redelivery (lazy-on-claim):** an `in_progress` message not acked within `VISIBILITY_TIMEOUT` becomes eligible for redelivery — the atomic claim query grabs `pending` rows **and** `in_progress` rows whose `claimed_at` is older than the timeout, so a crashed/restarted worker's task is recovered the next time any consumer polls. No scheduler is required; an optional background loop is only a backstop for messages stranded while nobody polls (see `design-decisions.md`, D15). Delivery is therefore **at-least-once**, so message handlers should tolerate the occasional duplicate.
* Senders receive a `message_id` (and the `session_id`). The task's response is **delivered back to the sender's inbox as a `kind="result"` message** (D20) via the same `check_inbox` long-poll, and also remains readable via `check_status` (the durable/secondary read).

### Multi-turn Clarification & Sessions (`input_required`)
* Every message carries a **`session_id`** that groups all turns of one exchange. `send_message` starts a new session (or continues a supplied one); the clarification round-trip below stays within it. (Adopted from A2A `contextId` / LangGraph `thread_id` / CrewAI flow id — see the survey in `design-decisions.md`, D17.)
* If a worker handling a claimed message needs more information, it calls **`request_input(message_id, question)`**: the original task is parked as **`input_required`** (it is **not** redelivered while parked), and a child `input_request` message carrying the question is enqueued back to the **original sender's** inbox, linked via `parent_id` + `session_id`.
* The sender receives the question through its normal `check_inbox` loop (and can also see it via `check_status` on the original message), then answers with `reply_to_message`. Completing that child answer **un-parks** the original task back to `pending` (the answer is appended to its `context`), so the worker re-claims it and finishes. The exchange can repeat for multiple rounds.
* This deliberately **reuses the existing inbox/reply machinery** — no special client support needed — and mirrors A2A's `input-required` + same-`taskId`/`contextId` resume pattern.

### Delivery / Notification
* Agents are CLI processes with no inbound port; the Hub **cannot push** to them. Delivery is therefore pull-based, with two complementary mechanisms:
* **Primary — long-poll `check_inbox`** (`wait=true` default, `timeout=N`): the call returns as soon as a message is available, or when the timeout elapses. An "on-duty" agent stays parked in a single tool call instead of spin-polling or waiting for a human nudge. (`wait=false` is a cheap one-off "anything for me?" check.) Implemented as an **async poll loop** — short off-loop `aiosqlite` checks every `LONGPOLL_INTERVAL` between `asyncio.sleep`s, never a blocking threadpool hold (`design-decisions.md` D21).
* Recommended agent work-loop: `register_agent` → `check_inbox(wait=true)` → handle → `reply_to_message` → repeat.
* **Result delivery (D20).** When a worker `reply_to_message`-completes a task, the hub enqueues a `kind="result"` message (carrying the response) to the **original sender's** inbox — so the requester learns the answer through its normal `check_inbox` long-poll (and the D19 hook peek nudges for it too) instead of spin-polling `check_status`. Best-effort: the result is marked `completed` in the same claim (no ack); the authoritative response stays on the task row for `check_status`.
* **Optional — hook peek/nudge layer (D19).** Both target clients expose lifecycle hooks (Claude Code: `Stop` / `UserPromptSubmit` / `SessionStart`; Antigravity `agy`: `StopHook` / `PreInvocationHook`, configured in `hooks.json`). A thin shipped script (`hook_peek.py`) calls the **read-only** `GET /api/peek?agent_id=…`, gets back a pending-count + sender summary, and injects a nudge ("you have N messages from X — call `check_inbox`") into the agent's context. The hook **peeks, never claims** — actual delivery and the ack still flow through `check_inbox` → `reply_to_message` / `fail_message`, so at-least-once is untouched. This makes delivery *feel* push-like (especially the Stop-hook variant, which keeps an agent in its loop rather than going idle) without coupling the hub to any client.
* **`GET /api/peek?agent_id=…`** is a plain (non-MCP) read-only JSON endpoint on the FastAPI app, sharing `db.py` (no second DB opener). It returns `{ count, senders: [...] }` mirroring exactly what `check_inbox` *would* claim (claimable `pending` rows incl. `input_request`; excludes parked `input_required`), but mutates nothing.
* **Honest limit:** a hook fires only on a *trigger* (tool call, invocation, stop, prompt submit). A truly idle agent waiting on a human won't see mail until its next trigger — the same fundamental constraint long-poll has. Waking a fully idle agent (OS interrupt / writing to its stdin) is out of scope (terminal-hijacking).

### Disconnect / Liveness
* `disconnect_agent` moves an agent's status to `offline`. The Hub **rejects new sends** to an explicitly-disconnected agent.
* **Staleness is distinct from disconnect.** A stale agent (missed heartbeat) may just be busy or mid-restart, so messages addressed to it are still **queued** and redelivered when it returns. Only an explicit disconnect blocks sends.
* **Send-to-stale is flagged, not silent (D6, refined per Q8).** When `send_message` queues to a *stale* recipient, the message is **marked** (`flagged_stale`) so the dashboard surfaces it distinctly (a "⚠ stale recipient" badge). Accepted and visible — rather than silently queued or rejected. The wider field leaves send-to-stale unsolved (see survey), so this is our own refinement.
* **Expiry (D6, extended per Q3).** A message left `pending` (never successfully claimed) longer than `MESSAGE_TTL` (24h default) is swept to the terminal **`expired`** state — so mail addressed to an agent that never returns self-cleans instead of accumulating. The sweep targets `pending` rows of **`kind='task'`** only — the derived `input_request` and `result` messages are excluded (D24, so expiring a child can't strand its parked parent), and a parked `input_required` task is **not** expired by this TTL in v1 (it waits on its clarification answer). Implemented as a lazy check plus the optional backstop loop — the same shape as the visibility-timeout reclaim (D15).

## 3. MCP Tool Definitions

The FastMCP server exposes the following tools (**9 total** — `request_input` added for the `input_required` branch, D17):

1. `register_agent(agent_id: str, skills: list[Skill], description: str | None = None) -> str`
   * Registers the agent as online and refreshes `last_seen`.
   * `skills`: Agent-Card-style list, each `{ id, name, description, tags?: list[str], examples?: list[str] }` (D16), validated by a Pydantic `Skill` model so the shape is enforced and advertised in `tools/list`. `description`: optional one-line agent summary.

2. `list_agents() -> list[dict]`
   * Returns each registered agent as `{ agent_id, description, skills, status, last_seen }`.

3. `send_message(sender_id: str, recipient_id: str, payload: str, context: str | None = None, session_id: str | None = None) -> dict`
   * Submits a new message/task from `sender_id` to the queue. Returns `{ message_id, session_id }` (a new `session_id` is minted when omitted; pass one to continue a thread).
   * `sender_id` is **recorded on the message** so `check_status` and the `input_required` round-trip (D17) can route back to the original requester. (Required corollary of D17; identity is unauthenticated per the trust model.)
   * **Validation:** rejects an unknown or explicitly-disconnected recipient; queues to a known-but-stale recipient **but sets `flagged_stale`** so the dashboard surfaces it (D6/Q8).

4. `check_inbox(agent_id: str, wait: bool = True, timeout: int = 30) -> list[dict]`
   * Atomically claims this agent's `pending` messages → `in_progress` (including `input_request` clarification messages and `kind="result"` notifications addressed to it; a `result` is delivered and marked `completed` in the same claim — no ack, D20). With `wait=True` (**the default** — D2), blocks up to `timeout` seconds for the first message to arrive, polling every `LONGPOLL_INTERVAL` (D21); `wait=False` is a cheap one-off check. Each returned message includes `session_id`, `parent_id`, and `kind`.

5. `reply_to_message(message_id: str, response: str) -> str`
   * Marks a message `completed` and attaches the response text (acks the claim).
   * **Result fan-out (D20):** if the replied message is a `task`, completing it enqueues a `kind="result"` message (carrying `response`) to the **original sender's** inbox, threaded by `session_id`/`parent_id`, so the requester is notified through its own `check_inbox` loop.
   * **Un-park rule (D17):** if the replied message is an `input_request` (its `parent_id` points to a parked `input_required` task), completing it flips the parent back to `pending` and appends the answer to the parent's `context`, re-queuing it to the worker.

6. `fail_message(message_id: str, error: str) -> str`
   * Marks a message `failed` with an error string (acks the claim). Completes the `failed` branch of the state machine.

7. `request_input(message_id: str, question: str) -> dict`  *(new — D17)*
   * Called by the agent currently handling a claimed message when it needs clarification. Parks that message as `input_required` (not redelivered while parked) and enqueues a child `input_request` message (the `question`) back to the **original sender's** inbox, linked by `parent_id` + `session_id`. Returns `{ request_message_id, session_id }`.

8. `check_status(message_id: str) -> dict`
   * Lets the original sender check status and read the response/error. When the message is `input_required`, also surfaces the pending `question` and its `request_message_id`. With D20 the response is also pushed to the sender's inbox as a `kind="result"` message, so `check_status` is the **durable/secondary** read (poll it if you didn't catch the result in your inbox).

9. `disconnect_agent(agent_id: str) -> str`
   * Marks the agent `offline` in the registry.

## 4. Web Dashboard Specifications

The dashboard is served at `http://localhost:8000/` (live data via `/api/state`; the MCP endpoint lives at `/mcp`). It features:
* **Header:** Status indicator of the Hub (Uptime, Total Messages).
* **Agents Panel:** A table listing all registered agents, their status (online / **stale** / offline, derived from `last_seen` + status), last-seen timestamp, optional `description`, and their **skills** (name + tags; full skill detail on hover/expand).
* **Message Queue Panel:** A live-updating table showing:
  * ID (and `session_id` / thread grouping)
  * Timestamp
  * Sender -> Recipient
  * Status Badge (Pending, In Progress, **Input Required**, Completed, **Failed**, **Expired**)
  * A **`kind`** indicator (task / **input_request** / **result**) so clarification questions and result notifications are distinguishable from tasks (D17/D20).
  * A **⚠ stale recipient** flag on messages sent to a stale agent (`flagged_stale`).
  * A button to view the full payload/response (and, for an `input_request`, the question) in a modal.
* **Activity Panel (D22):** a live feed of recent tool calls (tool name, agent-if-known, outcome, timestamp), backed by an in-memory ring buffer (last ~200 events) — the observability goal's activity stream, surfaced via `/api/state`.
* `/api/state` returns the recent messages (capped at `DASHBOARD_MESSAGE_LIMIT`) plus all agents, the recent **activity events** (D22), and header stats (uptime, total messages), and is polled every 2 seconds.

## 5. Storage / Persistence
* Database: `sqlite3` in **WAL mode** (concurrent reads during writes; far fewer "database is locked" errors).
* File: `hub.db` stored in the same directory as the server script.
* Schema:
  * `agents` table: `id` (PK), `description` (TEXT, nullable), `skills` (TEXT / JSON), `status`, `last_seen`.
  * `messages` table: `id` (PK), `session_id`, `parent_id` (nullable, threads `input_request`/`result` → parent task), `kind` (`task` | `input_request` | `result`, default `task`), `sender_id`, `recipient_id`, `payload`, `context`, `response`, `status` (`pending` | `in_progress` | `input_required` | `completed` | `failed` | `expired`), `flagged_stale` (INTEGER, default 0), `claimed_at`, `created_at`, `updated_at`.
  * Index on `messages(recipient_id, status)` for inbox queries; index on `messages(session_id)` for thread lookups; index on `messages(created_at)` for the dashboard's recent-messages ordering.
  * `skills` is serialized as JSON text (SQLite has no native array type).

## 6. Configuration Constants
Tunable values referenced above — `VISIBILITY_TIMEOUT` (600s), `STALE_THRESHOLD` (90s), the long-poll default `timeout` / `LONGPOLL_TIMEOUT` (30s), `LONGPOLL_INTERVAL` (~1s — the async poll cadence, D21), `DASHBOARD_MESSAGE_LIMIT` (100), and `MESSAGE_TTL` (86400s — the `pending`→`expired` sweep) — with their defaults and rationale are recorded in `design-decisions.md`.
