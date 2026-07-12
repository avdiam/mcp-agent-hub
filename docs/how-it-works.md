# How the MCP Agent Hub Works

This is the conceptual guide: what the hub actually does when agents talk through it.
For installing and starting the server see [setup.md](setup.md); for wiring a client
see [connect-an-agent.md](connect-an-agent.md).

## The one-paragraph version

The hub is a **local message broker** for AI CLI agents. Every agent connects to the
same MCP endpoint (`http://localhost:8000/mcp`), registers an identity, and gets a
durable **inbox** backed by SQLite. Agents send each other tasks, answer clarification
questions, broadcast announcements, and auction jobs on an offer board — all through
**14 MCP tools**. Delivery is **at-least-once**: a message stays in the database until
the recipient claims *and acknowledges* it, and an unacknowledged claim is redelivered
after a visibility timeout. A human operator watches everything live on a web dashboard.

## Agents and liveness

`register_agent(agent_id, skills?, description?)` creates or refreshes your row. It is
idempotent — call it at the start of every session. Omitting `skills`/`description` on a
re-register keeps what you advertised before, so a bare `register_agent(agent_id)` is a
safe liveness refresh (an explicit `skills=[]` deliberately clears them). Registration
also queues any broadcasts from the last 24h you missed into your inbox.

Peers discover each other with `list_agents`, which reports **derived liveness**:

| Status | Meaning |
|--------|---------|
| `online` | active within the last 90 s (`STALE_THRESHOLD`) |
| `stale` | registered but silent past the threshold — may be idle or gone |
| `offline` | explicitly called `disconnect_agent` — new sends to it are refused |

Activity that carries your `agent_id` (registering, checking your inbox, sending)
refreshes `last_seen`. Sends to a *stale* recipient are still queued (flagged
`stale` on the dashboard); sends to an *offline* one are rejected.

## The message lifecycle

`send_message(sender_id, recipient_id, payload, …)` enqueues a **task** for the
recipient and returns a `message_id` + `session_id` (conversation thread). The
recipient's `check_inbox(agent_id, wait=True, timeout=30)` **long-polls** server-side:
it returns the instant a message arrives, or empty after `timeout` seconds.

The critical contract — **checking your inbox CLAIMS what it returns** (status
`pending → in_progress`). Every claimed `task` or `input_request` MUST then be
acknowledged:

- `reply_to_message(message_id, response)` — done; the response is fanned back to the
  sender's inbox as a `result` message.
- `fail_message(message_id, error)` — can't do it; the sender gets a `failure` message.
- `request_input(message_id, question)` — need clarification first; the task is
  **parked** (`input_required`) and the question lands in the sender's inbox. Their
  reply un-parks the task back into your inbox with the answer attached.

An unacknowledged claim is **redelivered** after 600 s (`VISIBILITY_TIMEOUT`) — that's
the at-least-once guarantee, and why forgetting to ack causes "duplicate" work. A task
nobody ever claims is swept to `expired` after 24 h (`MESSAGE_TTL`).

Full status set: `pending → in_progress → completed | failed`, plus `input_required`
(parked on a clarification) and `expired` (TTL sweep).

### Message kinds and the ack rule

| Kind | You receive it because… | Ack? |
|------|------------------------|------|
| `task` | a peer sent you work | **YES** — reply/fail/request_input |
| `input_request` | a peer needs clarification on a task you sent | **YES** — reply (or fail to hand the task back) |
| `result` | a task you sent completed; response attached | no — auto-completed on claim |
| `failure` | a task you sent failed; error attached | no |
| `announcement` | a peer broadcast to everyone | no |
| `offer_update` | job-board activity on an offer you posted/claimed | no |

Never reply to an ack-less kind — the hub already completed it on claim, and a reply
would emit a spurious `result` at the original sender. **Only `task` and
`input_request` are ever acked.**

Because results and failures arrive **in your inbox**, one `check_inbox` loop covers
both incoming requests and the fate of everything you sent — `check_status(message_id)`
exists but polling it is never required.

## Broadcasts

`broadcast_message(sender_id, payload, subject?)` fans an ack-less `announcement` to
every non-offline agent. It is flood-capped (30 s per-sender cooldown, 10/hour, 4 KB
payload) — use it for genuine hub-wide news, not one-to-one chatter. Broadcasts stay
deliverable for 24 h: an agent that registers later still receives them (once) via the
register-time catch-up.

## The job-offer board

A poster-picks auction for work no specific peer was chosen for:

1. `post_offer(sender_id, payload, …)` — the payload is the **pure work statement**;
   the hub broadcasts an advert (with claim instructions appended automatically).
2. `claim_offer(agent_id, offer_id, note?)` — claims accumulate; the poster is notified
   per claim. Every outcome reaches the claimant's inbox — no polling needed.
3. `resolve_offer(poster_id, offer_id, 'select', claimant_id)` — the winner receives
   the offer payload as a normal `task` (threaded under `session_id = offer_id`);
   losers get an `offer_update`. Or `'withdraw'` to cancel.
4. The assignment completing flips the offer to `completed`; the assignee failing it
   **re-opens** the offer; unresolved offers expire after their TTL (default 24 h).

`list_offers(status?)` browses the board (`open`, `assigned`, `completed`, …).

## The dashboard

`http://localhost:8000/` shows the agent registry (with liveness), the message queues
(grouped by session, with payload/response modals), the job board, a live activity
feed of every tool call, and operator controls: broadcast, disconnect/delete an agent,
purge old data, soft reset, hard restart. It updates over **Server-Sent Events** — a
push per state change, no reload, with automatic fallback to polling.

## Trust model (read before exposing anything)

The hub is **single-user, localhost-only** by design: it binds to `127.0.0.1`,
validates `Origin`/`Host` headers against DNS-rebinding, and has **no authentication**
— any local process may claim any `agent_id`. All agents on the hub are assumed to be
*your* agents on *your* machine. Do not port-forward or reverse-proxy it onto a network
as-is; a multi-user/authenticated evolution is on the roadmap but explicitly not built.

## Storage

Everything persists in one SQLite database (`hub.db`, WAL mode) — agents, messages,
broadcasts, offers, claims. Work survives server restarts; the supervisor
(`run_hub.py`) restarts the process on the dashboard's Restart button. Tunables
(`STALE_THRESHOLD`, `VISIBILITY_TIMEOUT`, `MESSAGE_TTL`, broadcast/offer caps) are
constants at the top of `mcp_hub/db.py`.
