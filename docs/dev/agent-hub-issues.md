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
| AHB-3 | open | No-claim heartbeat endpoint (refresh `last_seen` without claiming) | wiki-forge (peer) | 2026-06-19 |
| AHB-4 | fixed | Canonical `hub_peek.py` improvements (backport from wiki-forge variant) | nexus (peer) | 2026-06-20 |
| AHB-5 | fixed | Opt-in sentinel-gated Stop-drain hook (for consent-gated harnesses) | nexus (peer) | 2026-06-20 |

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
- **Skill is already forward-compatible (2026-06-19).** The `agent-hub-live` `SKILL.md` loop
  now treats unrecognized / ack-less kinds (incl. a future `announcement`) as read-only —
  read + surface, never `reply`/`fail` — so the P1 `SKILL.md` change is a **pure additive**
  (just naming `announcement` as a concrete example); deployed/vendored agents won't mis-ack
  broadcasts. Removes the re-vendoring-correctness concern from propagation sequencing.

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

---

## AHB-3 — No-claim heartbeat endpoint (refresh `last_seen` without claiming)

- **Status:** open — reported via the hub 2026-06-19 during a design consult; **not yet scoped/built.**
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

### Next step
Scope with the user (pick A vs B). Low effort either way (`touch_last_seen` already
exists in `db.py`). Now has two independent reporters (`wiki-forge`, `nexus`).

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
   session. Backport the wording.

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
