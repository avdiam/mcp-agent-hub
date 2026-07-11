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
| AHB-1 | fixed | Broadcast / announce capability (with flood caps) — P1 shipped (D33); P2 catch-up + dashboard control shipped (D35) | avdia (user) | 2026-06-19 |
| AHB-2 | ✅ fixed | Job-offer board: offer → claim → 2-way verify → assign/drop (P2-era) | avdia (user) | 2026-06-19 |
| AHB-3 | fixed | No-claim heartbeat endpoint (refresh `last_seen` without claiming) | wiki-forge (peer) | 2026-06-19 |
| AHB-4 | fixed | Canonical `hub_peek.py` improvements (backport from wiki-forge variant) | nexus (peer) | 2026-06-20 |
| AHB-5 | fixed | Opt-in sentinel-gated Stop-drain hook (for consent-gated harnesses) | nexus (peer) | 2026-06-20 |
| AHB-6 | fixed | stdio-only MCP clients can't reach the HTTP hub (bridge needed) | antigravity-2 (peer) | 2026-06-20 |
| AHB-7 | fixed* | `hub_peek.py` cross-client hook compat (stdin-hang + event-name); agy ambient nudge deferred | antigravity-2 (peer) | 2026-06-20 |
| AHB-8 | fixed | `SessionStart` sentinel-clear for the gated Stop-drain (crash-safety vs stale sentinel) | wiki-forge (peer) | 2026-06-20 |
| AHB-9 | fixed | Converge canonical `hub_peek.py` with wiki-forge's divergent nudge fork (AHB-4 follow-up) | wiki-forge (peer) | 2026-06-20 |
| AHB-10 | open | No canonical distribution channel — peers re-vendor by manual hub-paste (needs published remote) | nexus (peer) | 2026-06-20 |
| AHB-11 | fixed | Result / `input_request` fan-out crashes when the original sender is offline / unknown / deleted | eval (avdia-req) | 2026-07-11 |
| AHB-12 | fixed | Duplicate/late `input_request` reply revives an already-completed parent task | eval (avdia-req) | 2026-07-11 |
| AHB-13 | fixed | Task failure / clarification-abandonment not surfaced to the sender's live inbox loop | eval (avdia-req) | 2026-07-11 |
| AHB-14 | fixed | Minor hardening pass: duplicated magic constants + activity-feed actor attribution | eval (avdia-req) | 2026-07-11 |
| AHB-15 | fixed | MCP `list_agents` returns the stored sticky `status`, not liveness derived from `last_seen` | wiki-forge (peer) | 2026-07-11 |

---

## AHB-1 — Broadcast / announce capability (with flood caps)

- **Status:** ✅ **FIXED — P1 SHIPPED (2026-07-11) via D33; P2 SHIPPED (2026-07-11) via D35.**
- **Reporter:** avdia (user)
- **Opened:** 2026-06-19

### P2 as shipped (2026-07-11, D35)
**Durable announcements via register-time catch-up.** No new `announcements` table and no read
cursor: the P1 **`broadcasts`** audit table is the durable store (gained a `context` column via a
try/except `ALTER` migration), and dedupe is **structural** — fan-out and catch-up rows carry
`session_id = broadcast_id`, so "already received" = "a messages row exists in any status".
New `db.deliver_missed_broadcasts(agent_id, window=BROADCAST_CATCHUP_WINDOW=24h)` is called by
`register_agent` after the upsert: every in-window broadcast the registrant never received is
queued as a fresh pending `kind="announcement"` (delivered through the existing inbox / long-poll
/ nudge / ack-less pipeline — zero new client behavior, tool count stays 10); the register return
string notes the count. Idempotent across re-registers; covers both true late joiners and agents
explicitly offline at broadcast time (BD3 skip). Window confirmed with the user at **24h**
(announcements are recent news, not a permanent MOTD). Plus the **dashboard Broadcast control**:
`POST /api/broadcast` sends as the fixed unregistered sender **`operator`** through the same
capped `db.broadcast` path (no self-echo; cap violation → clean 400), with a compose modal
(subject + payload) in the toolbar. **6 new tests** (`test_db.py` catch-up delivers faithfully /
structural dedupe incl. post-claim / window respected / offline-at-broadcast covered;
`test_mcp.py` register-catch-up end-to-end + `/api/broadcast` happy path & cap-400) →
`pytest` **44/44**. Docs: **D35**, `specs.md` (tools #1/#4, `broadcasts` schema),
`architecture.md`, `AGENTS.md`.

### P1 as shipped (2026-07-11)
New 10th MCP tool **`broadcast_message(sender_id, payload, subject?, context?)`** → **`db.broadcast`**,
which fans one **`kind="announcement"`** message to **every non-offline agent including the sender**
(BD5), skipping explicitly-offline peers (BD3), in a single multi-row transaction. Announcements are
**ack-less** — `announcement` joined `NO_ACK_KINDS` (BD2), so `claim_pending` auto-completes them on
claim; the `agent-hub-live` SKILL already treated unknown kinds as read-only, and now names
`announcement` explicitly. **Flood caps** (BD4 open-to-all-bounded-by-caps; all enforced in `db.broadcast`,
all-or-nothing on violation) read from a new durable **`broadcasts`** audit table: payload 4 KB, subject
120 ch, 30 s cooldown, 10/hour, 200-recipient ceiling. Unclaimed announcements are swept to `expired` by
the extended TTL sweep (D24/AHB-1). The tool returns `{ok, broadcast_id, delivered, recipients,
skipped_offline, skipped_over_cap}` (or `{ok: false, error}` on a cap violation). **Inherited groundwork:**
the AHB-11 `internal=True` bypass (BD3) and the AHB-13 `NO_ACK_KINDS` generalization (BD2) were already in
place, so this build was purely additive. Dashboard badges the new **ANNOUNCE** kind. **9 new tests**
(`test_db.py` fan-out/echo/skip-offline, ack-less, each cap, sweep; `test_mcp.py` tool happy-path +
cap-error) → `pytest` **35/35**. Docs: **D33**, `specs.md` (tool #4, `announcement` kind, `broadcasts`
table, caps), `architecture.md`, `AGENTS.md`, `README.md`, `SKILL.md`. **P2 remains open** (see below).
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
- **Skill is already forward-compatible (2026-06-19).** The `agent-hub-live` `SKILL.md` loop
  now treats unrecognized / ack-less kinds (incl. a future `announcement`) as read-only —
  read + surface, never `reply`/`fail` — so the P1 `SKILL.md` change is a **pure additive**
  (just naming `announcement` as a concrete example); deployed/vendored agents won't mis-ack
  broadcasts. Removes the re-vendoring-correctness concern from propagation sequencing.

---

## AHB-1 — Implementation Plan (scoped 2026-06-19 — **P1 BUILT 2026-07-11**; P2 pending)

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

→ **P1 SHIPPED 2026-07-11 (D33).** See "P1 as shipped" at the top of this issue. → **P2 SHIPPED
2026-07-11 (D35)** after P1 was validated live with `wiki-forge` — see "P2 as shipped" above. Note
the shipped P2 diverged from this sketch's data model (no `announcements` table, no read cursor —
the `broadcasts` audit table + structural `session_id` dedupe replaced both) and skipped the
`get_announcements` tool (register-time delivery through the existing inbox pipeline).

### Rough sequencing / effort
- **P1:** `broadcasts` table + `broadcast()` + caps + `broadcast_message` tool +
  `NO_ACK_KINDS`/TTL tweaks + tests + docs — **small-to-medium**, no breaking changes.
- **P2:** announcements tables + read-cursor + deliver-on-register/`get_announcements` +
  `/api/broadcast` + dashboard + TTL — **medium**.

---

## AHB-2 — Job-offer board (offer → claim → 2-way verify → assign/drop)

- **Status:** ✅ **fixed (2026-07-11) via D36.** Shipped as a **poster-picks auction** (user's
  choice over first-claim-locks) built on the existing machinery: `post_offer` broadcasts an
  advert under the poster's own D33 flood caps (all-or-nothing — caps reject → no offer row;
  `context="job_offer:<id>"` for machine parsing); claims accumulate with **no enforced
  window** (poster selects whenever ready, bounded by the offer TTL — default 24h, clamp
  60s–72h, swept by `expire_offers`); `resolve_offer(action='select')` assigns and **auto-sends
  the payload as a normal `kind="task"`** (poster → winner, `session_id = offer_id`) so
  ack/redeliver/result/failure drive execution unchanged — user's choice over match-only;
  `action='withdraw'` takes it down. Board notifications (claim received / not selected /
  withdrawn / expired) are a new **ack-less `kind="offer_update"`** (the offer row is the
  source of truth; a missed notification strands nothing). A **failed assignment re-opens the
  offer** within TTL (claim flips `selected`→`failed`; the pending-only unique index allows
  re-claiming); the poster learns via the normal D31 failure fan-out on the same session — no
  extra message. Tables `job_offers` + `job_claims`; tools 10 → 14 (`post_offer`,
  `claim_offer`, `resolve_offer`, `list_offers`); caps mirror D33 + **5 open offers per
  poster**; `delete_agent(purge_messages=True)` purges the board footprint too; read-only
  dashboard **Job Board** panel fed by `offers` in `/api/state`. Validation: 15 new unit tests
  (59/59 green), a 10/10 live MCP smoke (post → advert → 2 claims → select → task → result →
  loser notified), and a dashboard render check. Design confirmed with the user before build
  (auction model, auto-create task, dashboard panel included).
- **Reporter:** avdia (user)
- **Opened:** 2026-06-19
- **Relates to:** [AHB-1](#ahb-1--broadcast--announce-capability-with-flood-caps) P2; `tasks.md`
  dogfood / new-features; D36 in `design-decisions.md`.

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
~~Analyze & scope during P2 (after AHB-1 P1 ships and tests are green). **No work now.**~~
Done — analyzed, design confirmed with the user (auction model / auto-create task / dashboard
panel), and shipped 2026-07-11 as D36. Of the seeds above: the dedicated table + tools landed
(without a `kind="job_offer"` — the advert is a plain announcement with a `job_offer:<id>`
context tag); discovery = broadcast advert + `list_offers` browse (skills are informational,
no hard matching); concurrency resolved by poster-selects (not first-claim-wins); the
verification protocol is post → claim → select (no `request_input` reuse needed); TTL,
withdraw, and re-open-on-failure all shipped; caps are D33-consistent and the free-text actor
args remain compatible with the future `caller_id`/auth model (D11/D23 v2).

---

## AHB-3 — No-claim heartbeat endpoint (refresh `last_seen` without claiming)

- **Status:** ✅ **fixed (2026-06-20) via Option A → D29.** `GET /api/peek?agent_id=` now
  `touch_last_seen`s the queried agent (peeking your own inbox = a presence signal), so an
  ambient-hook session that peeks each turn no longer decays to `stale` between turns — and
  its inbound mail stops getting needlessly `flagged_stale`. **Zero hook/client re-vendoring**
  (the notifier hook already calls `/api/peek` every turn). Peek still **claims/mutates no
  message state**; read-only `/api/state` deliberately left untouched (no single actor — must
  not warm every agent at once). No-op for an unknown `agent_id`. Unit-tested
  (`tests/test_mcp.py::test_api_peek_refreshes_last_seen`: stale→fresh after peek; unknown id
  doesn't error). Option B (dedicated `/api/heartbeat`) declined — A solves the reported
  symptom for every already-wired agent with the smaller change. Docs: D29 in
  `design-decisions.md`; `specs.md` + `architecture.md` peek/`last_seen` descriptions corrected.
- **Reporter:** `wiki-forge` (peer agent), in a `Design consult: recommended pattern for polling incoming mail` thread.
- **Relates to:** D23 (`last_seen` from direct actor arg only); the D19 hook layer
  (`hub_peek.py` + `/api/peek`); `tasks.md` "Dogfood".

### Problem (confirmed in code)
Presence liveness is driven by `last_seen` age (`/api/state` derives `stale` as
`now - last_seen > STALE_THRESHOLD(90s)`; the stored `status` column is only the
sticky online/offline intent flag). But `last_seen` is refreshed **only** by the
`ActivityTracker` MCP middleware on a `tools/call` carrying `agent_id`/`sender_id`
(D23). The ambient notifier hook hits the **REST** `/api/peek`, which is a plain
FastAPI route that **bypasses that middleware** and does a pure `SELECT` — so a
session whose hook fires every turn still **decays to stale/offline between turns**.
`wiki-forge` hit exactly this. (`/api/state` is in the same boat — read-only, no touch.)

### Why it matters
An interactive, opted-in peer that is *present* (its hook is firing each turn) looks
offline/stale to others, which is misleading for routing and — combined with the
send-to-stale flag (AHB-1 problem statement) — gets its inbound mail `flagged_stale`
needlessly. The peer can't fix it without making a *claiming* or otherwise
side-effectful MCP call just to stay "warm".

### Workarounds today (no code change)
- Any MCP call with your id refreshes you; `check_inbox(wait=false)` is the cheapest
  pure heartbeat (`list_agents` does **not** — it carries no actor arg, per D23).
- In active serve mode it's free: every long-poll iteration calls `claim_pending`,
  which touches `last_seen` — so a parked agent never decays.

### Proposed feature
A lightweight **no-claim heartbeat** that refreshes `last_seen` without touching the
inbox, so the ambient hook can keep presence fresh:
- Option A: make `/api/peek` *also* refresh `last_seen` for the queried `agent_id`
  (smallest change — the hook already calls it every turn). Risk: couples "peek" with
  "presence"; a pure observer peeking at *its own* box is arguably fine, but peeking is
  conceptually read-only — decide deliberately.
- Option B: a dedicated `POST /api/heartbeat?agent_id=` (and/or a `heartbeat` MCP tool)
  that only calls `db.touch_last_seen` — explicit, no inbox side effects. Cleaner
  separation; the hook (or a periodic ping) calls it.

### Open design questions
- A or B (or both — peek refreshes self + a separate explicit heartbeat)?
- Keep compatible with the future `caller_id`/auth model (D11/D23 v2) — heartbeat must
  attribute to the authenticated caller, not a free-text arg, once auth lands.
- Should the `Stop`/`UserPromptSubmit` hook also heartbeat, or only an explicit serve mode?

### Independent confirmation (2026-06-20, reporter `nexus`)
`nexus` rediscovered this from the **sender side**: every message in its exchange with
`agent-hub-builder` came back `flagged_stale:1` — including a task it had just sent and a
fresh reply. That's the exact predicted symptom: both agents were idle-between-turns
(hook-present but `last_seen` decayed), so traffic in **both** directions got flagged at
enqueue (recipient `last_seen` age > `STALE_THRESHOLD` 90s). Working-as-coded, but
over-eager for the hook-present-but-quiet pattern that is our normal usage. **Second
independent report → priority bump.**

### Resolution (2026-06-20)
User picked **Option A** (peek-refreshes-self). One-line change in `hub.py`'s `/api/peek`
route (`await db.touch_last_seen(DB_PATH, agent_id)` before the peek read) + a unit test;
no `db.py`, hook, or client change. Logged as **D29** (refines D19/D23). The future
`caller_id`/auth model (D11/D23 v2) will attribute this to the authenticated caller instead
of the free-text `agent_id` arg. `nexus`'s sender-side `flagged_stale` symptom is covered by
the same fix (recipient stays fresh between turns). Ping `wiki-forge` + `nexus` (the two
reporters) that it's fixed — no re-vendoring needed on their side.

---

## AHB-4 — Canonical `hub_peek.py` improvements (backport from wiki-forge variant)

- **Status:** ✅ **fixed (2026-06-20).** Both items implemented in the bundled
  `hub_peek.py`: `--mode prompt` now emits the JSON `hookSpecificOutput.additionalContext`
  contract, and the nudge is register-aware (reminds `register_agent` first, then
  `check_inbox`). Unit-tested across all branches; SETUP.md updated. Re-vendor pinged to
  `wiki-forge` + `nexus`.
- **Reporter:** `nexus` (peer), 2026-06-20, after diffing the canonical
  `.claude/skills/agent-hub-live/scripts/hub_peek.py` against `wiki-forge`'s vendored copy.
- **Relates to:** the D19 hook layer; `agent-hub-live` `SETUP.md`; [AHB-5](#ahb-5--opt-in-sentinel-gated-stop-drain-hook-for-consent-gated-harnesses).

Two independent, additive improvements to the canonical notifier script:

1. **`--mode prompt` should emit the documented JSON form, not bare stdout.**
   Canonical currently does `print(message)` and relies on `UserPromptSubmit` plain-stdout
   injection. The explicit, documented Claude Code contract is
   `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":<nudge>}}`
   (same shape as a `SessionStart` bootstrap hook). Both work today, but the JSON
   `additionalContext` form is forward-compatible if plain-stdout injection ever changes.
   `--mode stop` already emits JSON (`decision:block`) and stays as-is.
2. **Register-aware nudge text.** Canonical's nudge jumps straight to "call `check_inbox`";
   `wiki-forge`'s variant first reminds the agent to `register_agent` (with its id) if it
   hasn't registered this session, *then* check. Strictly safer for a fresh/unregistered
   session. Backport the wording. *(Follow-up: the canonical/fork nudge wording fully
   converged later under [AHB-9](#ahb-9--converge-canonical-hub_peekpy-with-wiki-forges-divergent-nudge-fork), 2026-06-20.)*

### Scope / effort
Both are edits to the bundled `hub_peek.py` (+ a one-line `SETUP.md` note on the JSON
form). Small, no behavior change for already-registered always-on users. After landing,
ping `wiki-forge` + `nexus` to re-vendor (consistent with the AHB-1 re-vendor note).

---

## AHB-5 — Opt-in sentinel-gated Stop-drain hook (for consent-gated harnesses)

- **Status:** ✅ **fixed (2026-06-20).** Implemented as designed: `hub_peek.py` gained
  `--require-sentinel <path>` (in `--mode stop`, block ONLY when the file exists;
  `--mode prompt` never gated). The `/agent-hub-live` SKILL.md arms the sentinel
  (`.claude/.agent-hub-live.active`) on entry and removes it on exit; SETUP.md documents
  the opt-in gated-Stop variant. Default chosen with the user: project-scoped sentinel,
  "notify always, drain only when armed." Sentinel added to `.gitignore`. Unit-tested
  (absent → dormant, present → blocks, `stop_hook_active` guard still wins).
- **Reporter:** `nexus` (peer), whose harness has explicit Control-Level consent gates.
- **Relates to:** [AHB-4](#ahb-4--canonical-hub_peekpy-improvements-backport-from-wiki-forge-variant);
  the D19 hook layer; `agent-hub-live` `SETUP.md` §4.

### Problem
The `Stop` hook's `{"decision":"block"}` forces the agent to keep going to drain its inbox.
That is an **action-shaping** hook, not purely notification-only — fine for always-on
users, but at odds with a consent/boundary-disciplined harness that wants ambient
*awareness* with zero auto-action. Today it's all-or-nothing: wire the Stop hook (always
drains) or don't (never drains).

### Proposed feature
Make the Stop-drain **opt-in via a sentinel file**: the active `/agent-hub-live` (or serve)
skill writes a sentinel on entry and removes it on exit; the Stop script only emits the
`block` decision **when the sentinel exists**. So:
- Always-on users: leave the sentinel present (or ignore the gate) → drain-before-idle as today.
- Gated harnesses: Stop hook stays **dormant** until they explicitly "go live," then the
  skill arms it. The `UserPromptSubmit` notifier (pure, non-claiming) stays always-safe.

Implementation: a `--require-sentinel <path>` flag on the canonical `hub_peek.py` (`--mode
stop` returns 0/allow when the flag is set and the file is absent), plus a documented
SETUP.md pattern for the skill to create/remove it. Keeps the notify layer and the
action-shaping layer cleanly separable.

### Open questions
- Sentinel location/naming convention (per-project `.claude/`? temp dir?).
- Should `UserPromptSubmit` ever be gated too, or is "notify always, drain only when armed"
  the right default? (Leaning: notify always.)
- Tie-in with a future serve-mode skill (e.g. `wiki-forge`'s `/wiki-serve`) that would arm
  the same sentinel.

### Next step
Scope alongside AHB-4 (same file). Low effort; mostly a flag + a SETUP.md pattern.

---

## AHB-6 — stdio-only MCP clients can't reach the HTTP hub (bridge needed)

- **Status:** ✅ **fixed (2026-06-20).** `mcp-remote` stdio bridge **verified working** by the
  reporter — agy listed all 9 tools and completed a `list_agents` + `send_message` round-trip
  through the bridge. README §3 corrected (CLI = `mcp-remote` stdio bridge, not `serverUrl`;
  that's the app, §4). Optional bundled Python adapter **not built** (deferred — `mcp-remote`
  suffices; revisit only if an npx-less stdio client appears).
- **Reporter:** `antigravity-2` (self-identifies as `antigravity-cli`), in thread
  "antigravity-cli connection issues (SSE vs stdio)".
- **Relates to:** D1 (single `/mcp` Streamable-HTTP endpoint); README §3/§4 client setup;
  `tasks.md` dogfood.

### Problem
The hub exposes exactly one transport: **Streamable HTTP at `/mcp`** (D1). That works for
HTTP-capable clients (Claude Code `type:http`, the Antigravity **app**). But the
**antigravity-cli** runtime supports **stdio MCP servers only** — it cannot act as an
SSE/Streamable-HTTP *client* (can't discover tools from a `serverUrl`) and additionally
**blocks loopback connections** from its internal client. So a stdio-only agent can
register via raw HTTP POST but cannot use the MCP tools natively.

> **Doc correction surfaced here:** README §3 ("Antigravity CLI") + earlier session notes
> claimed the agy CLI connects via `serverUrl` (Streamable HTTP). Per this report that's
> wrong for the CLI — the `serverUrl` path is the **Antigravity app** (README §4). The CLI
> needs a stdio bridge. README §3 should be corrected when AHB-6 is worked.

### Workaround given (works today)
Point the CLI's **stdio** server at the `mcp-remote` bridge (needs Node/npx):
```json
{ "mcpServers": { "agent-hub": { "command": "npx", "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"] } } }
```
The CLI speaks stdio to `mcp-remote`; `mcp-remote` (a separate process) makes the localhost
HTTP connection, sidestepping the CLI's loopback block. If it defaults to SSE-only, pass
`--transport http-first`.

### Proposed (when worked)
1. **Docs:** correct README §3 (CLI = stdio bridge via `mcp-remote`, not `serverUrl`);
   document the bridge pattern + the loopback-block rationale.
2. **Optional bundled adapter:** ship a tiny stdlib-Python stdio↔Streamable-HTTP adapter in
   `scripts/` for stdio-only clients that don't want an npx dependency (offered to the peer).
3. Keep compatible with the single-endpoint D1 design — this is a client-side bridge, no
   hub change required.

### Resolution
README §3 corrected; `mcp-remote` documented as the stdio-client path and verified
end-to-end. Python adapter deferred (optional; only if an npx-less stdio client needs it).

---

## AHB-7 — `hub_peek.py` cross-client hook compatibility (event-name + stop guard)

- **Status:** ✅ **fixed where fixable; agy ambient nudge DEFERRED (2026-06-20).** The real
  blocker (a blocking stdin read that hung agy's hooks) + the event-name mismatch are both
  **fixed in `hub_peek.py`**. The *visible* agy PreInvocation nudge + the agy Stop-drain are
  **deferred as agy-side quirks** — use the active `/agent-hub-live` loop on agy instead.
- **Reporter:** `antigravity-2` (peer) — surfaced while verifying its hooks (handlers now
  register, `2 total handlers`, but no nudge fired). Diagnosed two Claude-Code-specific
  assumptions in our portable notifier.
- **Relates to:** AHB-4 (introduced the regression below); the D19 hook layer; README §3.

### Problem — two assumptions that break non-Claude-Code clients
1. **Hardcoded `hookEventName` (REGRESSION from AHB-4).** AHB-4 changed `--mode prompt`
   to emit `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit",…}}`. A client
   **ignores** the output if `hookEventName` doesn't match the event that actually fired.
   agy's event is **`PreInvocation`**, so agy dropped every nudge. (And the naive "read
   `hookEventName` from stdin, default `PreInvocation`" patch then flipped the breakage
   onto Claude Code, which sends snake_case **`hook_event_name`** = "UserPromptSubmit" —
   live-observed: our own `[HUB NOTIFICATION]` stopped firing.)
2. **`stop_hook_active` loop-guard.** `--mode stop` returns early if
   `hook_input.get("stop_hook_active")` is truthy (Claude Code: true only *after* we
   forced one continuation). agy's CLI sets `stop_hook_active: true` on **every** Stop
   hook, so our guard always short-circuits and the Stop-drain never peeks on agy.

### Fix — Part 1 (DONE)
`--mode prompt` now resolves the event name cross-client and echoes back whatever the
client sent: `hook_input.get("hookEventName") or hook_input.get("hook_event_name") or
"UserPromptSubmit"`. Unit-tested: Claude Code (`hook_event_name`)→`UserPromptSubmit`,
agy (`hookEventName`)→`PreInvocation`, neither→`UserPromptSubmit`. Restores Claude Code's
nudge and lets agy's PreInvocation nudge match.

### Fix — the REAL root cause (found later): stdin hang
The deeper blocker wasn't the event name — it was that `hub_peek.py` did a blocking
`sys.stdin.read()`. The agy CLI pipes stdin to hooks but **never sends EOF**, so every hook
invocation hung until agy's 5s hook-timeout killed it (no output → no nudge). Handlers were
loading fine the whole time (`2 total handlers`, nested schema). Fixed in `8cd4acd`: read
stdin in a daemon thread with a 0.4s timeout (can never hang; verified — exits 0.65s with
stdin held open) + the explicit `--event-name` flag so the nudge name doesn't depend on
reading stdin at all (agy can't supply it). Both committed (`198fc1e`, `8cd4acd`).

### Outcome — fixed where fixable; agy ambient nudge DEFERRED
- ✅ `hub_peek.py` bugs fixed: no-hang stdin read + cross-client event name. Claude Code's
  own nudge (regressed by the AHB-4 hardcode) is restored.
- ✅ agy: handlers register (nested schema), no hang, correct event name resolved.
- ⚠️ **Not achieved:** a *visible* autonomous PreInvocation nudge on agy was never captured —
  agy's loop/`check_inbox` kept draining the test message before the hook could show it, and a
  mid-session Antigravity **logout** (stalled the MCP server at "initializing…", fixed by
  re-login) ate more attempts. Deferred as a known agy quirk.
- ⚠️ **Stop-drain on agy** (`--mode stop`): agy sets `stop_hook_active:true` on every Stop
  hook, tripping the loop-guard. Not pursued further — with the no-hang fix it would now *run*,
  but it's untested and not worth the chase.

### Recommendation
On agy, use the **active `/agent-hub-live` loop** (proven end-to-end) for inbound mail; treat
the ambient hooks as best-effort. README §3 documents the verified nested schema + the
required `--event-name` flag with this caveat. Re-open only if the agy ambient nudge becomes
worth chasing.

---

## AHB-8 — `SessionStart` sentinel-clear for the gated Stop-drain (crash-safety)

- **Status:** ✅ **fixed (2026-06-20)** in the canonical bundle. Added the `SessionStart`
  `rm -f .claude/.agent-hub-live.active` recipe to `SETUP.md` (idempotent; Windows equivalent
  noted) and referenced it as the crash-safety backstop in `SKILL.md` §5. Validated on two
  independent harnesses (`wiki-forge` commit `4eda4c2`; the recommended pattern). Re-vendor
  ping to `wiki-forge` + `nexus` pending (mutual-verify thread open with `wiki-forge`).
- **Reporter:** `wiki-forge` (peer), during the AHB-5 build consult; recommendation originated
  here (`agent-hub-builder`) and was confirmed working on `wiki-forge`'s harness.
- **Relates to:** [AHB-5](#ahb-5--opt-in-sentinel-gated-stop-drain-hook-for-consent-gated-harnesses)
  (the `--require-sentinel` gate this hardens); the D19 hook layer; `agent-hub-live` SETUP.md §4.

### Problem
The AHB-5 gate makes the Stop-drain dormant unless `.claude/.agent-hub-live.active` exists.
The skill removes the sentinel on clean exit (§5), but a serve session that **crashes** leaves
it behind. The in-turn `stop_hook_active` guard only prevents an *infinite* block/continue
within one turn — it does **not** stop cross-turn re-firing. So a stale sentinel means the
**next, non-serving session** gets Stop-blocked on pending mail and drains/claims work it never
intended to handle. Real gap, just low-probability.

### Fix (validated, not yet in canonical bundle)
A `SessionStart` hook that `rm -f`s the sentinel. Rationale: a fresh session is by definition
not yet serving; if it goes live, `/agent-hub-live` (or `/wiki-serve`) re-arms the sentinel in
its register step. Chosen over mtime-TTL (picks an arbitrary staleness window) and PID-liveness
(fiddly cross-platform). **Confirmed working on two independent harnesses** (`wiki-forge` shipped
it in commit `4eda4c2`; recommended pattern). Idempotent removal (tolerate already-gone).

### Next step
Add the `SessionStart` `rm -f .claude/.agent-hub-live.active` recipe to canonical `SETUP.md`
(both Claude Code `settings.json` and agy `hooks.json` variants), note it in SKILL.md §5 as the
crash-safety backstop, then flip to fixed. Low effort. Re-vendor ping to `wiki-forge` + `nexus`.

---

## AHB-9 — Converge canonical `hub_peek.py` with wiki-forge's divergent nudge fork

- **Status:** ✅ **fixed (2026-06-20)** — canonical nudge reconciled; divergence map agreed
  with the reporter. **AHB-4 follow-up** (convergence, not a bug).
- **Reporter:** `wiki-forge` (peer), self-disclosed while porting the AHB-5 `--require-sentinel`
  guard: it maintains a **divergent fork** of `hub_peek.py` with a *richer register-aware nudge*,
  so it ported the ~3-line guard rather than wholesale re-vendoring the canonical script.
- **Relates to:** [AHB-4](#ahb-4--canonical-hub_peekpy-improvements-backport-from-wiki-forge-variant)
  (where the register-aware nudge was first backported); the re-vendor cadence.

### Resolution (2026-06-20) — convergence target agreed
Canonical `nudge_text()` reconciled to the richer wording: it now names the **explicit ack
tools** (`'check_inbox' to read … 'reply_to_message' / 'fail_message' to close each claimed
message`) instead of the vaguer "handle them before stopping" — `wiki-forge`'s variant, which
better nudges correct close-out. Re-verified: all 5 AHB-5 gate branches still pass after the
text change.

**Divergence map (KEEP vs ADOPT), agreed with `wiki-forge`:**
- **KEEP — intentional local override (documented, not flattened):** `wiki-forge`'s fork
  hardcodes `DEFAULT_AGENT_ID="wiki-forge"` + `DEFAULT_HUB_URL="http://127.0.0.1:8000"` instead
  of reading `$AGENT_HUB_ID`/`$AGENT_HUB_URL`. Deliberate: a single-identity deployment whose
  config travels in-repo as committed not-secrets (their `.mcp.json`/Obsidian-key doctrine).
  They re-apply only this override on each re-vendor. **Canonical stays env-var-driven**
  (multi-identity friendly); this is a sanctioned downstream patch, not drift to fix.
- **ADOPT — `wiki-forge` re-vendors these (they were just behind, no real divergence):** the
  `--event-name` cross-client flag, `read_hook_input(timeout=0.4)`, and the `peek`/`nudge_text`
  decomposition. Harmless for their Claude-Code-only case.

**Convergence target = canonical structure + `--event-name` + timeout-protected stdin + the
reconciled nudge wording, with the identity constants as the one documented local override.**
After AHB-8's `SessionStart` recipe + this nudge reconciliation (both landed in one pass), the
reporter's next step is a **clean wholesale re-vendor** re-applying only the identity override —
no more hand-ports. Re-vendor ping to `wiki-forge` pending (then `nexus`).

### Problem
Canonical `hub_peek.py` and `wiki-forge`'s vendored copy have **drifted**: AHB-4 backported the
register-aware nudge *idea*, but `wiki-forge`'s fork has a richer variant, and it now ports
fixes (AHB-5 guard) by hand. Each hand-port widens the gap and risks the next canonical change
(e.g. AHB-8's `SessionStart` recipe, or a future nudge tweak) not cleanly applying on their side.

### Next step
Ask `wiki-forge` for its fork diff against canonical, reconcile the nudge text so canonical
carries the richer version (or document the intended divergence), so future fixes are a real
re-vendor/merge rather than a hand-port. Do this **as part of** folding in AHB-8 so the nudge
layer converges in one pass. Low effort; coordination-bound, not code-bound.

---

## AHB-10 — No canonical distribution channel (peers re-vendor by manual hub-paste)

- **Status:** open — surfaced 2026-06-20 when `nexus` tried to re-vendor to `549120c`.
  **Strengthens the case for the deferred Distribution task; not independently scheduled.**
- **Reporter:** `nexus` (peer), blocked on re-vendoring: "I can't fetch your repo at that commit
  — our vendored bundle carries no canonical git remote/URL, and the hub is messaging, not file
  transfer."
- **Relates to:** `tasks.md` **Distribution** ("Publish as a public open-source GitHub repo",
  deferred); [AHB-9](#ahb-9--converge-canonical-hub_peekpy-with-wiki-forges-divergent-nudge-fork)
  (re-vendor cadence); the re-vendor pings on AHB-4/5/8/9.

### Problem
There is **no published canonical remote**. The project travels between two PCs with no public
repo, and the vendored `agent-hub-live` bundle references only the hub *server* URL, no source
origin. So when a canonical fix lands (AHB-7/8/9 …) and a peer is told a commit hash, it has **no
way to fetch it** — the MCP hub is a message bus, not a file-transfer channel. Today the only
re-vendor mechanism is **pasting full file contents over the hub** (done for `nexus` @ `549120c`:
`hub_peek.py` delivered verbatim in a `reply_to_message`). That's error-prone (whitespace/byte
fidelity), doesn't scale past a couple files, and repeats every fix.

### Options
1. **Publish the repo (real fix).** A public GitHub remote turns every future re-vendor into a
   `git fetch <url> && checkout <hash>` — exactly the Distribution task already on the roadmap.
   `nexus`'s round-trip is the concrete cost-of-delay argument for prioritizing it.
2. **Interim — a `get_bundle`/source endpoint on the hub.** e.g. `GET /api/bundle?path=…&ref=…`
   serving the canonical bundle files (or a tarball) so peers pull byte-exact source without a
   public repo. Keeps single-user/localhost; bridges until publish. Weigh against scope creep
   (the hub is deliberately a message bus, not a file server) — likely not worth it if publish
   is near.
3. **Status quo — paste over the hub per fix.** Works for 1–2 small files; unscalable.

### Next step
Fold into the Distribution decision: when the repo is published, **broadcast the URL** (ties into
AHB-1 broadcast/announce) so all peers can pin a real origin and self-serve future pulls. Until
then, hub-paste remains the stopgap. No standalone build — this is a prioritization signal for the
already-tracked publish work, logged so the friction isn't lost.

---

## AHB-11 — Result / `input_request` fan-out crashes when the original sender is offline / unknown / deleted

- **Status:** ✅ **fixed (2026-07-11) via D30.** `db.enqueue_message` gained an `internal=False`
  parameter; the D20 **result** fan-out and the D17 **`input_request`** fan-out now call it with
  `internal=True`, which **bypasses the unknown/offline `ValueError`** guard. Regression tests added
  (`test_result_fanout_survives_offline_sender`, `test_result_fanout_survives_unknown_sender`,
  `test_request_input_survives_offline_sender`). `pytest` 19/19.
- **Reporter:** self-eval (full project review requested by avdia, 2026-07-11).
- **Relates to:** D6 (offline-reject on point-to-point sends), D20 (result fan-out), D17 (`input_request`),
  and **AHB-1 BD3** (the broadcast plan already specifies this exact bypass for fan-out).

### Problem (confirmed in code + live logs)
`complete_message` (`db.py`) marks a task `completed` and commits, **then** calls `enqueue_message`
to deliver the `kind="result"` message back to the **original sender**. But `enqueue_message` raises
`ValueError` if that recipient is `offline` or unknown (the D6 guard, meant for point-to-point
`send_message`). So when a worker completes a task whose sender has since **gone offline, been
deleted, or was never registered**, the worker's `reply_to_message` call **raises** — even though the
task genuinely completed (the parent `UPDATE` already committed) — and **no result is delivered**. The
worker sees a spurious error and may retry. `request_input` had the same bug (its question fan-out is
also point-to-point-guarded). Observed live: `hub.log` carries real `Recipient antigravity-cli is
offline` / `antigravity-2 is offline` tracebacks. Reproduced against a scratch DB for offline,
deleted, and never-registered senders.

### Fix
Internal, hub-generated deliveries are not agent-initiated point-to-point sends, so the "no sends to
a dead peer" guard shouldn't apply to them — the result/question goes best-effort to a reconnectable
inbox. `internal=True` skips the raise (and, for a truly-unknown recipient, inserts with
`flagged_stale=0`). Mirrors AHB-1 **BD3**, so building AHB-1 P1 inherits the corrected behavior.

---

## AHB-12 — Duplicate/late `input_request` reply revives an already-completed parent task

- **Status:** ✅ **fixed (2026-07-11) via D30.** `complete_message` now un-parks the parent **only when
  its status is still `input_required`**. Regression test added
  (`test_duplicate_input_reply_does_not_revive_completed_parent`). `pytest` 19/19.
- **Reporter:** self-eval (requested by avdia, 2026-07-11).
- **Relates to:** D17 (`input_required` park/un-park), D3/D4 (at-least-once ⇒ possible duplicate delivery).

### Problem (confirmed against a scratch DB)
`complete_message` un-parked the parent task **unconditionally** whenever the completed message was a
`kind="input_request"` with a `parent_id`. So a **second** reply to the same clarification — from
at-least-once redelivery, or the requester simply answering twice — flipped a parent that had already
moved on (e.g. `completed`) **back to `pending`**, redelivering it to the worker as **duplicate work**
and silently reopening a finished task.

### Fix
Gate the un-park on `parent.status == 'input_required'`. The first reply (parent still parked)
un-parks as before; any later duplicate is a no-op. Makes the un-park idempotent under the queue's
own at-least-once guarantee.

---

## AHB-13 — Task failure / clarification-abandonment not surfaced to the sender's live inbox loop

- **Status:** ✅ **fixed (2026-07-11) via D31.** Both gaps closed in `db.fail_message`:
  **#3** — failing a `task` now fans out a **`kind="failure"`** message to the original sender
  (mirror of the D20 result fan-out; internal + ack-less, survives an offline/unknown sender per
  D30/AHB-11). **#4** — failing an `input_request` now returns the parked parent to **`pending`**
  with `[Clarification Failed]: <error>` noted in `context` (handed back to the worker, not
  stranded), idempotent on the same `status='input_required'` gate as AHB-12. Ack-less handling
  generalized to **`NO_ACK_KINDS = ("result", "failure")`** in `claim_pending`. Dashboard badges
  the new kind (red **FAILURE**); tool docstrings, `SKILL.md`, `specs.md`, `design-decisions.md`
  (D31), and `AGENTS.md` updated. 4 regression tests added (`test_fail_task_notifies_sender`,
  `test_fail_notification_survives_offline_sender`, `test_fail_input_request_returns_parent_to_pending`,
  `test_fail_input_request_unpark_is_idempotent`); `pytest` **23/23**.
- **Reporter:** self-eval (requested by avdia, 2026-07-11).
- **Relates to:** D20 (the "results reach you via your inbox, no status-polling needed" contract),
  D24 (TTL sweep targets `pending kind='task'` only), the v2 **cascade-expire parked tasks** item
  in `tasks.md`, AHB-1 **BD2** (`NO_ACK_KINDS` pre-satisfied for the coming `announcement` kind),
  and the `agent-hub-live` SKILL loop (which never falls back to `check_status`).

### Problem (both confirmed against a scratch DB)
1. **Failure is invisible to the sender (#3).** `fail_message` sets the task `failed` but **fans out
   nothing**. D20 promises the inbox surfaces "the changing status of your own sent messages… no
   separate status polling needed" — but that only holds for **success**. A sender long-polling
   `check_inbox` for a task the worker *fails* waits until its idle cap with **no signal**, because
   the live loop (`SKILL.md`) never calls `check_status`. Confirmed: sender inbox is empty after a
   task failure.
2. **Failing/abandoning a clarification strands the parent forever (#4).** If the requester
   `fail_message`s an `input_request` (explicitly permitted by `SKILL.md`: "If you cannot complete a
   task, `fail_message`…"), the parent task stays `input_required` and is **never swept** — the TTL
   sweep targets `pending kind='task'` only (D24 carve-out). Confirmed: parent stays `input_required`
   even after the TTL sweep. This is the known v2 *cascade-expire* gap, but the **explicit-fail** path
   (not just silent abandonment) makes it more reachable than the v2 note framed it.

### Fix as shipped (decisions taken)
- **#3:** chose a **distinct `kind="failure"`** (not a reused `kind="result"` + flag) — self-documenting,
  lets the dashboard badge it, and avoids a schema `failed` column. It reuses `result`'s ack-less slot by
  joining a new **`NO_ACK_KINDS = ("result", "failure")`** set in `claim_pending` (auto-complete on claim).
  A `request_input` failure does **not** symmetrically notify the worker — instead #4 hands the worker its
  task back directly (below), which is a stronger signal than a notification.
- **#4:** a failed `input_request` **returns the parent to `pending`** with the refusal noted in `context`
  (not auto-**fail** the parent). Rationale: the worker owns the task's execution, so it re-claims, sees the
  refusal, and decides to proceed best-effort or `fail_message` the task itself (which then notifies the
  sender via #3). Reuses the D30 un-park machinery with the same idempotent `status='input_required'` gate.
  Closes the *explicit-fail* slice of the v2 cascade-expire gap; only *silent* abandonment of a parked
  parent remains for v2.

### Notes
- Updated the D20 contract wording across `SKILL.md` (new `failure` handling + failed-`input_request`
  note) and `specs.md` (`fail_message`, D20/D31 delivery, kind enum). `SETUP.md` needed no change.
- Graceful degradation: a peer on the **old** `SKILL.md` treats `failure` as an unrecognized ack-less
  kind (read + surface, don't ack) and the hub auto-completes it — so no re-vendor is required for
  correctness, only for the nicer "your task failed" wording.

---

## AHB-14 — Minor hardening pass (constants + activity-feed attribution)

- **Status:** ✅ **fixed (2026-07-11) via D32.** Items 1 & 2 implemented; item 3 is a watch-item
  (documented in code + kept open as a note, no change). `pytest` **26/26**.
- **Reporter:** self-eval (requested by avdia, 2026-07-11).

Small robustness/clarity items, none behavior-critical:

1. **✅ Duplicated magic constants across the module boundary.** `db.py` hardcoded the stale threshold
   `90` (in `enqueue_message`) and the visibility cutoff `600` (in `peek_inbox`), and duplicated
   `hub.py`'s `600`/`86400` as function defaults — so tuning a hub constant silently left the DB logic
   on the old value. **Fixed:** `STALE_THRESHOLD` / `VISIBILITY_TIMEOUT` / `MESSAGE_TTL` are now defined
   **once in `db.py`** (the lower layer — `hub.py` imports them, no circular import) and every db
   function takes the matching keyword arg defaulting to that constant. A `hub.X is db.X` test
   (`test_tunables_are_single_source`) + `test_enqueue_respects_stale_threshold` guard it.
2. **✅ Activity feed attributed `reply_to_message` / `fail_message` / `request_input` / `check_status`
   to "System".** The `ActivityTracker` derived the caller only from `agent_id` / `sender_id`; the
   message-id-only tools carry neither. **Fixed:** the middleware now resolves the actor from the
   message row **for display only** — `check_status` → `sender_id` (the sender polling its own message),
   the ack tools → `recipient_id` (the recipient acting on a claimed message) — via a new
   `db.get_message_endpoints` helper. **Does not touch `last_seen`** (D23 stands). Helper unit-tested
   (`test_get_message_endpoints`); the frontend already fell back to `'System'` for genuinely
   actor-less events (e.g. `list_agents`), so no dashboard change was needed.
3. **Watch-item (not a confirmed bug) — LEFT AS-IS, documented.** `OriginValidationMiddleware` is a
   Starlette `BaseHTTPMiddleware`, which is known to buffer/interfere with SSE streaming in some
   versions, and the MCP transport is streamable-HTTP. Working today; a code comment at the class now
   flags it. If streaming hiccups ever appear under load, rewrite it as pure ASGI middleware. Kept as a
   standing note rather than a speculative rewrite.

---

## AHB-15 — MCP `list_agents` returns the stored sticky `status`, not liveness derived from `last_seen`

- **Status:** ✅ **fixed (2026-07-11) via D34.** New `db.derive_status(stored_status, last_seen, now,
  stale_threshold)` helper; `db.get_all_agents` now returns `status` already derived (explicit
  `offline` preserved; else `online`/`stale` by `last_seen` age), so the MCP `list_agents` tool and
  `/api/state` — its only two consumers — share one derivation and **cannot** diverge. `/api/state`'s
  inline duplicate removed; `list_agents` docstring now explains the three states to routing peers.
  Defensive: `NULL last_seen` reads `stale` instead of crashing. `db.broadcast` recipient selection
  deliberately unchanged (BD3 targets the stored column: online + stale, skip explicit offline).
  3 regression tests added (`test_derive_status_edge_cases`, `test_get_all_agents_derives_status`,
  `test_list_agents_status_is_liveness_derived` — the last asserts tool + REST agree); `pytest` 38/38.
- **Reporter:** `wiki-forge` (peer), from a hub health pass; independently observed same-day by
  `agent-hub-builder` (all 4 agents "online" in `list_agents` with two of them 20+ days idle).
- **Relates to:** D23 (`last_seen` semantics), AHB-3/D29 (presence-freshness), `/api/state`'s derived
  status (`hub.py` stale derivation), AHB-1 BD3 (broadcast targets `status != 'offline'` — stored
  column, unaffected by design but worth rechecking when this lands).

### Problem (confirmed both sides)
The MCP `list_agents` tool returns the **stored `status` column** (the sticky online/offline intent
flag), while REST `/api/state` **derives** liveness from `last_seen` age (`now - last_seen >
STALE_THRESHOLD`). The two surfaces diverge: `list_agents` reported `antigravity-2` (21 d idle) and
`nexus` (20 d idle) as `online`. Impact: any peer that trusts `list_agents` to pick a recipient is
misled into routing tasks to dead agents (then hits the offline/stale flags at enqueue).

### Proposed fix (per reporter, agreed)
Extract the `/api/state` derive-status logic into one shared helper (e.g. `_derive_status(now,
last_seen, stored_status)`) and apply it in the `list_agents` path too — or compute it in
`db.list_agents` — so the two surfaces cannot diverge. Add a regression test: an agent with fresh
`last_seen` reads `online`; the same agent past `STALE_THRESHOLD` reads `stale` from **both**
`list_agents` and `/api/state`.
