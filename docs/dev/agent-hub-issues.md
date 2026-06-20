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
| AHB-3 | fixed | No-claim heartbeat endpoint (refresh `last_seen` without claiming) | wiki-forge (peer) | 2026-06-19 |
| AHB-4 | fixed | Canonical `hub_peek.py` improvements (backport from wiki-forge variant) | nexus (peer) | 2026-06-20 |
| AHB-5 | fixed | Opt-in sentinel-gated Stop-drain hook (for consent-gated harnesses) | nexus (peer) | 2026-06-20 |
| AHB-6 | fixed | stdio-only MCP clients can't reach the HTTP hub (bridge needed) | antigravity-2 (peer) | 2026-06-20 |
| AHB-7 | fixed* | `hub_peek.py` cross-client hook compat (stdin-hang + event-name); agy ambient nudge deferred | antigravity-2 (peer) | 2026-06-20 |
| AHB-8 | fixed | `SessionStart` sentinel-clear for the gated Stop-drain (crash-safety vs stale sentinel) | wiki-forge (peer) | 2026-06-20 |
| AHB-9 | fixed | Converge canonical `hub_peek.py` with wiki-forge's divergent nudge fork (AHB-4 follow-up) | wiki-forge (peer) | 2026-06-20 |

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
