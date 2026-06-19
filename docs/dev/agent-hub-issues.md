# Agent Hub — Tracked Issues & Requests

> **Maintainer intake log.** `agent-hub-builder` is the point of contact for the MCP
> Agent Hub. Friction, bugs, and feature requests — reported by peer agents over the hub
> (`recipient_id: agent-hub-builder`) or by the user — land here as tracked issues, and we
> work them off. This project travels between PCs with **no local Claude memories**, so
> anything worth preserving lives here, in `tasks.md` (roadmap), or `sessions.md` (history).
> Update this file in the same change as any fix.

**Status lifecycle:** `open` → `in-progress` → `fixed` (or `wont-fix` / `duplicate`).
**ID scheme:** `AHB-<n>`, monotonically increasing.

| ID | Status | Title | Reporter | Opened |
|----|--------|-------|----------|--------|
| AHB-1 | open | Broadcast / announce capability (with flood caps) | avdia (user) | 2026-06-19 |

---

## AHB-1 — Broadcast / announce capability (with flood caps)

- **Status:** open
- **Reporter:** avdia (user)
- **Opened:** 2026-06-19
- **Relates to:** `tasks.md` "New features" (priorities/broadcast — to be scoped); the
  maintainer-announcement need (see `register_agent` description for `agent-hub-builder`).

### Problem
There is **no broadcast primitive**, and `send_message` **rejects offline recipients**
(`enqueue_message` raises `ValueError` if the recipient is `offline`). So an agent — e.g.
the maintainer wanting to announce "contact me for hub topics" — cannot reach everyone:
a one-shot loop over `list_agents` + `send_message` only hits agents that are **online
right now**, and agents that connect **later** never receive it. Today the maintainer role
is advertised only passively via the `agent-hub-builder` registry description (seen when a
peer calls `list_agents`).

### Proposed feature
A first-class way to send one message to many agents. Two complementary pieces:
1. **Broadcast send** — e.g. a `broadcast_message` MCP tool (and/or `POST /api/broadcast`)
   that fans a payload out to all (or all online) registered agents.
2. **Durable announcements / MOTD** *(optional, stronger)* — persist announcements so an
   agent that connects **after** the broadcast still receives them (delivered on
   `register_agent`, or via a `get_announcements` tool / returned by `list_agents`). This
   is what makes "everyone eventually learns X" actually hold for late joiners.

### Hard requirement — flood protection (caps)
Broadcast must **not** let anyone flood the server/agents. Design must include:
- **Per-sender rate limit / cooldown** (e.g. N broadcasts per time window).
- **Max recipients per broadcast** and/or fan-out batching.
- **Payload size cap.**
- **Who may broadcast** — consider restricting to a maintainer/allowlist or an opt-in flag,
  rather than any agent at will.
- Sensible interaction with the offline-recipient rule (broadcasts likely shouldn't fail
  the whole call just because some recipients are offline — skip/queue per policy).

### Open design questions
- Tool vs. HTTP-only vs. both? (Agents need the MCP tool; the dashboard might use HTTP.)
- Does a broadcast count toward each recipient's inbox as a normal `kind` (e.g. a new
  `kind="announcement"`), or a separate channel that doesn't require ack?
- Durability/TTL of announcements; dedupe so a reconnecting agent isn't re-nudged forever.
- Keep compatible with the future `caller_id`/auth model (D11/D23 v2) for "who may broadcast".

### Notes
- Until built, maintainer presence is discoverable via the `agent-hub-builder` description.
