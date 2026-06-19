# Agent Hub — Tracked Issues & Requests

> **Maintainer intake log.** `agent-hub-builder` is the point of contact for the MCP
> Agent Hub. Friction, bugs, and feature requests — reported by peer agents over the hub
> (`recipient_id: agent-hub-builder`) or by the user — land here as tracked issues, and we
> work them off. This project travels between PCs with **no local Claude memories**, so
> anything worth preserving lives here, in `tasks.md` (roadmap), or `sessions.md` (history).
> Update this file in the same change as any fix.

**Status lifecycle:** `open` → `scoped` → `in-progress` → `fixed` (or `wont-fix` / `duplicate`).
**ID scheme:** `AHB-<n>`, monotonically increasing.

| ID | Status | Title | Reporter | Opened |
|----|--------|-------|----------|--------|
| AHB-1 | scoped | Broadcast / announce capability (with flood caps) | avdia (user) | 2026-06-19 |
| AHB-2 | open | Job-offer board: offer → claim → 2-way verify → assign/drop (P2-era) | avdia (user) | 2026-06-19 |

---

## AHB-1 — Broadcast / announce capability (with flood caps)

- **Status:** scoped — **P1 confirmed & ready to build** (2026-06-19); P2 deferred. Not yet implemented.
- **Reporter:** avdia (user)
- **Opened:** 2026-06-19
- **Relates to:** `tasks.md` "New features" (priorities/broadcast — to be scoped); [AHB-2](#ahb-2--job-offer-board-offer--claim--2-way-verify--assigndrop) (P2-era job board); the
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

---

## AHB-1 — Implementation Plan (scoped 2026-06-19, NOT yet built)

### Scope & phasing
- **P1 — Broadcast-to-connected (MVP).** One MCP tool fans a message out to all currently
  connected agents, gated by flood caps + an audit table. Satisfies the stated need: *any
  agent can broadcast when needed, but caps prevent flooding.*
- **P2 — Durable announcements / MOTD (later).** Persist announcements so agents that
  connect *after* a broadcast still receive them, plus a dashboard control. Ship P1 first,
  validate, then decide on P2.

### Locked design decisions (confirm the ⚠ ones before building)
- **BD1 — Delivery = fan-out as a new `kind="announcement"`.** Insert one message row per
  recipient, reusing the existing `check_inbox` claim path. Mirrors the D20 result fan-out;
  no new delivery machinery. Bounded by caps.
- **BD2 — Ack-less, auto-complete on claim.** Like `kind="result"` (D20): announcements
  auto-complete when claimed, so recipients never have to `reply`/`fail`. Generalize
  `claim_pending`'s result-auto-complete to a `NO_ACK_KINDS = {"result", "announcement"}`.
- **BD3 — Target online + stale, skip explicitly-offline.** Bypass `enqueue_message`'s
  offline-reject (that guard is for point-to-point). A broadcast must **never fail the whole
  call** because some recipients are offline — skip them and return counts.
- **BD4 ⚠ — Authorization = open to all registered agents, controlled by caps (not an
  allowlist).** Matches the user's intent ("all agents could send a broadcast when needed").
  Abuse is prevented by rate limits + payload caps below, not by gating who may call it.
  *Optional tightening (note, not P1):* an operator kill-switch env var and/or a per-agent
  `can_broadcast` flag, evolving to the `caller_id`/auth model (D11/D23 v2).
- **BD5 — Echo to sender (confirmed 2026-06-19).** Include the sender among recipients —
  the sender receives its own broadcast too. (Ack-less auto-complete means the echo doesn't
  clutter the sender's inbox.)
- **BD6 ⚠ — P2 durability is a separate phase.** P1 reaches only the connected set.

### Data model changes
- **New `broadcasts` audit table** — doubles as the durable rate-limit source (survives
  restarts, unlike an in-memory bucket): `id, sender_id, subject, payload, recipient_count,
  created_at`.
- **New message kind `announcement`** (no schema change — `kind` is a free-text column).
  Add it to `NO_ACK_KINDS` and include it in the TTL sweep (see edge cases).
- **(P2 only)** `announcements` (`id, sender_id, subject, payload, created_at, expires_at`)
  + a per-agent read cursor (`announcement_reads(agent_id, announcement_id)` or a
  `last_announcement_seen` column on `agents`).

### New surface
- **`db.py`**
  - `broadcast(db_path, sender_id, payload, subject=None, context=None)` → enforce caps →
    fan-out insert (online+stale, minus sender) in one transaction (`@retry_on_lock`) →
    insert `broadcasts` audit row → return `{"delivered": n, "skipped_offline": m,
    "recipients": [...], "broadcast_id": ...}`.
  - `_check_broadcast_rate(db, sender_id)` → query `broadcasts` for the sender within the
    window; raise a clear `ValueError` on violation (no rows inserted).
  - Generalize the auto-complete in `claim_pending` to `NO_ACK_KINDS`.
  - Extend `expire_messages` to also sweep unclaimed `kind='announcement'`.
  - *(P2)* `add_announcement(...)`, `get_unseen_announcements(agent_id)`, `mark_seen(...)`.
- **`hub.py`**
  - New MCP tool **`broadcast_message(sender_id, payload, subject=None, context=None)`**
    (tool count 9 → 10). Maps `ValueError` (cap/auth) to a clean tool error string.
  - *(P2)* `POST /api/broadcast` + a dashboard "Broadcast" control; `get_announcements`
    tool and/or deliver-unseen on `register_agent`.

### Flood caps (concrete defaults — new constants in `hub.py`, passed into `db`)
- `BROADCAST_MAX_PAYLOAD = 4096` bytes; `BROADCAST_MAX_SUBJECT = 120` chars.
- `BROADCAST_MIN_INTERVAL = 30` s (per-sender cooldown between broadcasts).
- `BROADCAST_HOURLY_CAP = 10` per sender per rolling hour.
- `BROADCAST_MAX_RECIPIENTS = 200` (safety ceiling; fan-out batched if ever exceeded).
- Violations return a descriptive error and insert **nothing** (all-or-nothing).

### Delivery semantics
- `kind="announcement"`, ack-less, best-effort-once (auto-complete on claim, per BD2).
- Recipients = all agents with `status != 'offline'`, **including the sender** (BD5 echo, confirmed).
- Surfaced through the normal `check_inbox` long-poll **and** the existing `/api/peek`
  nudge — so the `UserPromptSubmit`/`Stop` notifier hooks already cover announcements with
  no change.

### Edge cases & interactions
- **TTL (D24):** the current sweep targets `pending kind='task'` only. Add
  `kind='announcement'` so an announcement an agent never claims doesn't linger forever.
  (`result` is already short-lived via auto-complete.)
- **Dashboard:** render `kind='announcement'` distinctly; optional "broadcasts" stat tile.
- **`last_seen` (D23):** broadcasting refreshes the sender's `last_seen` (it's the direct
  `sender_id` actor arg).
- **WAL/concurrency:** fan-out is a single multi-row transaction under `@retry_on_lock`
  (consistent with the D21 stability fixes).
- **At-least-once:** auto-complete-on-claim means a rare duplicate is possible (same as
  results); announcements must be idempotent to read — they are (informational).

### Testing plan
- **`tests/test_db.py`:** fan-out reaches online+stale but not offline and not the sender;
  each cap (payload, subject, cooldown, hourly) rejects with no rows written; audit row
  recorded; announcement auto-completes on claim (no ack); unclaimed announcement expires
  via the extended sweep.
- **`tests/test_mcp.py`:** `broadcast_message` happy path; cap violation returns a clean
  error; `check_inbox` surfaces the announcement and needs no `reply`/`fail`.

### Docs to update when built
`specs.md` (new tool, `announcement` kind, `broadcasts` table, caps), `architecture.md`,
`design-decisions.md` (assign the next **D-number**, e.g. D29), `AGENTS.md` (tool count,
kinds, caps, NO_ACK_KINDS), `README.md` (tool list), the `agent-hub-live` SKILL/SETUP
(mention broadcast), and flip this issue `scoped → in-progress → fixed`.

### Confirmed answers (2026-06-19) — P1 ready to build
1. **BD4 — open-to-all-with-caps.** ✅ Confirmed. No allowlist; abuse controlled by caps only.
2. **Scope — P1 first, then P2.** ✅ Build P1, get its tests green, **then** take up P2
   durability (and the AHB-2 job board) as a follow-up phase.
3. **Cap defaults** (30 s cooldown, 10/hour, 4 KB payload, 200 recipients). ✅ Accepted.
4. **Echo to sender.** ✅ **Yes** — the sender receives its own broadcast (BD5 updated).
5. **Ack-less auto-complete-on-claim.** ✅ Accepted for now; revisit during P2.

→ **P1 is fully specified and unblocked.** Implement when the user gives the go-ahead.

### Rough sequencing / effort
- **P1:** `broadcasts` table + `broadcast()` + caps + `broadcast_message` tool +
  `NO_ACK_KINDS`/TTL tweaks + tests + docs — **small-to-medium**, no breaking changes.
- **P2:** announcements tables + read-cursor + deliver-on-register/`get_announcements` +
  `/api/broadcast` + dashboard + TTL — **medium**.

---

## AHB-2 — Job-offer board (offer → claim → 2-way verify → assign/drop)

- **Status:** open — idea captured 2026-06-19; **analyze/design during the P2 timeframe**,
  after AHB-1 P1 lands and its tests pass. Do NOT build yet.
- **Reporter:** avdia (user)
- **Opened:** 2026-06-19
- **Relates to:** [AHB-1](#ahb-1--broadcast--announce-capability-with-flood-caps) P2; `tasks.md`
  dogfood / new-features.

### Concept
A lightweight job/task marketplace on the hub:
1. An agent posts a **job offer** — work open to *anyone*, not addressed to a specific
   recipient (announcement/broadcast-style), describing the task + the skills it needs.
2. Any **relevant or free** agent can **submit/claim** it (express intent to take it).
3. A **two-way verification** handshake between poster and claimant confirms the match
   (both sides explicitly accept).
4. On agreement the offer is **marked assigned** and removed from the open board so no one
   else picks it up; if no match (withdrawn / claimant declines / times out) it is **dropped**.

### Why it's distinct from AHB-1
AHB-1 broadcast is fire-and-forget one-to-many *information*. This is a stateful,
**claimable work item** with a lifecycle (open → claimed → verifying → assigned/dropped),
**competition** among multiple claimants, and a **mutual-accept** step — closer to a task
queue / auction than to an announcement. It likely builds *on top of* AHB-1's broadcast for
the "post to everyone" step, then adds the claim+verify state machine.

### Design seeds to analyze later (NOT decisions)
- New entity, e.g. `job_offer(id, poster_id, required_skills, payload, status, claimant_id,
  created_at, expires_at)` — a dedicated table + tools, possibly with a `kind="job_offer"`
  for the broadcast step.
- **Discovery:** skills-match vs. browse-the-board; ties into `skills` + `list_agents`.
- **Concurrency / no double-assign:** first-claim-wins vs. poster-selects-among-claimants;
  atomic claim like `claim_pending`.
- **Two-way verification protocol:** reuse the `request_input`/`reply` handshake, or a
  dedicated accept/confirm pair.
- **Lifecycle / TTL:** auto-drop stale offers; allow withdraw; re-open if the claimant fails.
- **Anti-abuse:** caps consistent with AHB-1; keep compatible with the future
  `caller_id`/auth model (D11/D23 v2).

### Next step
Analyze & scope during P2 (after AHB-1 P1 ships and tests are green). **No work now.**
