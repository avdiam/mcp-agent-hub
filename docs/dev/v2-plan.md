# V2 Plan — Sequencing the Deferred Ideas

> **What this is.** Triage + sequencing for the **"Possible future / v2 (deferred)"** bucket in
> [`tasks.md`](tasks.md). That list grew into a flat bucket of mixed granularity — one-liners
> (condition-notify wakeup) next to full design memos
> ([`mem/leveraging_multi_agent_frameworks.md`](mem/leveraging_multi_agent_frameworks.md),
> [`mem/acp-evaluation.md`](mem/acp-evaluation.md)), buildable-now items next to explicitly
> trigger-gated ones. This file groups them into tracks, states each item's gate, effort, and
> dependencies, and recommends an order.
>
> **What this is not:** a status tracker. `tasks.md` stays the source of truth for what's left;
> an item picked up from here gets scoped as an **AHB issue** in
> [`agent-hub-issues.md`](agent-hub-issues.md) and, when built, a **D-number** in
> [`design-decisions.md`](design-decisions.md). Written 2026-07-12 — after AHB-10 (publish)
> closed the last open issue except AHB-19, and stress round 2 came back green.

## Principles (these drive the ordering)

1. **Honor the gates.** Several items carry explicit "revisit only on X" rulings from past
   triage (pooling, ContentBlock, auth). A gate is a decision already made — don't build early
   just because the item is old.
2. **Single-user / localhost stays the product** (roadmap, 2026-06-18). Design every new
   surface to stay compatible with the future `caller_id`/auth model (D11/D23 v2), but don't
   build auth or networking until multi-user is actually on the roadmap.
3. **Dogfood-driven.** The whole AHB-16/17/18 class came from *first real runs*, not
   speculation. Prefer items validated by live peer friction; let the next board/peer runs
   confirm or kill the speculative ones (AHB-19 is the test case).
4. **Reuse what v1 built.** Two items got structurally cheaper since they were deferred:
   D38's `StateNotifier` is exactly the in-process wakeup signal the condition-notify item
   was waiting for, and D17's park machinery is the base permission delegation extends.

## Tracks

| Track | Theme | Items |
|-------|-------|-------|
| **A** | Queue lifecycle & retention (correctness debt) | A1 cascade-expire parked tasks · A2 persisted events + terminal-row GC |
| **B** | Typed protocol polish (ACP-derived) | B1 typed cancel/reject outcome · B2 typed `stop_reason`/fail-category |
| **C** | Board polish | C1 AHB-19 advisory claim window |
| **D** | Performance (gated) | D1 notifier-based long-poll wakeup · D2 connection pooling |
| **E** | Interop & visualization (framework-concepts memo) | E1 A2A card endpoint · E2 Mermaid session tracer · E3 git-native task helpers · E4 permission delegation |
| **F** | Auth / multi-user runway | F1 `caller_id`/auth **design spike** (document, don't build) |
| — | Parked with named triggers | P1 MCP `ContentBlock` · P2 auto-continue Stop hook |

## Recommended order

1. **C1 / AHB-19** — smallest concrete item and the only open issue. Fold into the next board
   dogfood run with the 4-agent roster; let that run confirm the gap (or uphold the AHB-17 #1
   YAGNI ruling and close it wont-fix).
2. **Track A (one build)** — the real correctness debt: cascade-expire + events persistence +
   terminal-row GC as one coherent `db.py`/sweeper change, regression-tested in the D30–D37
   style.
3. **Track B** — small additive win for peers and the dashboard. Do it **before** E4 so the
   typed outcome shape exists for permission replies to reuse.
4. **E1 + E2** — A2A card endpoint and Mermaid tracer. High demo value now that the repo and
   docs site are public (E1 makes wiki-forge's `agent-card-shape-on-mcp-hub` wiki page
   literally true); E2 also counts toward workstream 2's open UI list.
5. **D1** — notifier-based long-poll wakeup, with a before/after stress comparison against the
   round-2 baselines as the acceptance gate.
6. **E4** — permission delegation. Largest item; run a design consult with peers over the hub
   first (as done pre-AHB-2), then build.
7. **F1** — auth design spike: write the design down, build nothing.
- **D2, P1, P2** stay parked behind their triggers (see the table at the bottom). **E3** is a
  cheap client-side utility that can slot in anywhere a dogfood task wants it.

---

## Per-item briefs

### A1 — Cascade-expire parked `input_required` tasks — **S/M**
- **Why:** the last hole in the lifecycle. D24 deliberately excluded `input_request`/`result`
  from the TTL sweep; D31 (AHB-13 #4) closed the *explicit-fail* path, but a **silently
  abandoned** clarification still strands its parent `input_required` forever.
- **Design seed:** extend the existing `expire_messages` sweep: a parent parked longer than a
  TTL (reuse `MESSAGE_TTL` unless dogfood argues for a shorter park TTL) has its pending
  `input_request` child expired and the exchange cascade-closed. Scoping decision: flip the
  parent to terminal `expired` (nobody answered for 24 h) vs return-to-pending with a note
  (D31-consistent). Leaning `expired` + a `failure`-style fan-out to the sender (D31 #3
  machinery) so nobody waits on a dead exchange.
- **Dependencies:** none. **Tests/docs:** `test_db.py` sweep cases (parked-past-TTL cascades;
  answered-in-time untouched; idempotent), `specs.md` lifecycle, D-number.

### A2 — Persisted `events` table + retention / terminal-row GC — **M**
- **Why:** the activity log is an in-memory ring buffer (D22) that vanishes on restart, and
  nothing ever deletes old `completed`/`failed`/`expired` rows. Two *invisible* growth vectors
  named in the 2026-06-16 triage: an abandoned `pending kind='result'` (requester never
  re-checks) and a never-read `input_request` — prefer **GC/delete over a `state=expired`
  patch** (already ruled in `tasks.md`).
- **Design seed:** an `events` table written where the `ActivityTracker`/`StateNotifier` bump
  sites already are; a retention window (e.g. 7 d events, 30 d terminal messages — confirm
  with avdia); GC runs in the same backstop loop as `expire_messages`/`expire_offers`.
  Dashboard activity feed gains restart-surviving history for free.
- **Dependencies:** none (pairs naturally with A1 in one build). **Tests/docs:** GC respects
  windows, never touches `pending`/`in_progress`/`input_required` rows; `architecture.md`,
  `specs.md` schema, D-number.

### B1 — Typed cancel/reject `outcome` for clarifications — **S/M**
- **Why:** a sender who wants to *abandon* a parked exchange today can only `fail_message` the
  `input_request` (free text). ACP's `session/request_permission` models this as a typed
  `outcome` (`selected` vs `cancelled`) — the clean shape per
  [`mem/acp-evaluation.md`](mem/acp-evaluation.md) §Q3.1.
- **Design seed:** an optional typed `outcome` on the reply/fail path for `input_request`
  (e.g. `answered | cancelled | rejected`), stored on the row, surfaced in the parent's
  `[Clarification …]` context note and as a dashboard badge. Free-text prose stays the payload.
- **Dependencies:** none; **E4 reuses this enum** for permission replies. **Tests/docs:**
  reply-with-outcome round-trip, un-park note wording; `specs.md`, `SKILL.md`, D-number.

### B2 — Typed `stop_reason` / fail-category enum — **S**
- **Why:** `failed`/`expired` rows carry only free-text `error`; the dashboard can't
  distinguish a refusal from a crash from a timeout. ACP's `stop_reason` enum validated the
  shape (`mem/acp-evaluation.md` §Q3.3).
- **Design seed:** additive nullable column (e.g. `refused | error | timeout | cancelled |
  expired`), optional arg on `fail_message`, set automatically by the sweeps
  (`expired`) and by B1 (`cancelled`). Free text remains alongside.
- **Dependencies:** none; ship with B1 as one "typed semantics" build. **Tests/docs:** enum
  stored + defaulted, dashboard badge; `specs.md`, D-number.

### C1 — AHB-19: advisory `claim_window_seconds` on `post_offer` — **S**
- **Why:** claimants can't tell if the poster picks in two minutes or two days; posters have
  no convention for how long to hold the auction ([AHB-19](agent-hub-issues.md#ahb-19--job-board-no-claim-window-signal-claim-window-hint-field)).
- **Design seed:** optional advisory field on `post_offer`, surfaced in the advert broadcast,
  the board row / `list_offers`, and `claim_offer`'s return next to `expires_at`. **No
  enforcement** — auto-select-at-T is a separate, bigger decision.
- **Gate:** the next contested board run showing the gap again (vs the AHB-17 #1 YAGNI
  ruling). **Tests/docs:** field threads through all three surfaces; `specs.md`, D-number,
  flip AHB-19.

### D1 — Notifier-based long-poll wakeup — **M**
- **Why:** `check_inbox(wait=True)` polls every ~1 s (D21, which explicitly deferred
  condition-notify to v2). Handoff latency between chatty agents is bounded by that poll.
- **Design seed:** **reuse D38's `StateNotifier`** — it is already bumped on every tool call,
  mutating REST endpoint, and effective sweep. The long-poll loop awaits the notifier (global
  epoch bump → re-check own inbox) with the existing sleep interval kept as a backstop
  timeout, so a lost wakeup degrades to today's behavior instead of hanging.
- **Acceptance gate:** before/after stress comparison against the round-2 baselines —
  **51 ops/s** writer-contention (canonical params) and **~70 HTTP calls/s**
  (`http_loadtest.py --preregister`) — plus a p50 task-handoff latency win. No merge on
  correctness regressions (0 double-claims stays the hard gate).
- **Dependencies:** D38. **Tests/docs:** wakeup-vs-timeout branches, `architecture.md` D21
  refinement, D-number.

### D2 — DB connection pooling / shared connection — **M/L, HARD-GATED**
- **Gate (unchanged from 2026-06-18):** build only on **multi-user** or real, sustained
  throughput pressure. Round 2 reconfirmed single-user headroom (~70 calls/s, p95 < 2 s).
- **Evidence the cost is real when the gate is met:** the D37 regression — one extra
  `_connect()` per completion cost ~12 calls/s (16 %) — is a measured datapoint that per-call
  connect overhead matters; it just doesn't matter *yet* at this scale.
- **Design seeds already weighed** (`tasks.md`): one shared write-conn + read pool (keeps WAL
  1-writer/N-reader) vs a single `asyncio.Lock`-guarded connection (simplest). Compare against
  the recorded 51 ops/s writer baseline.

### E1 — A2A card endpoint `/api/agents/{agent_id}/card` — **S**
- **Why:** D16 already stores A2A-shaped `skills[]`; serving a spec-shaped `agent-card.json`
  is a read-only mapping with real interop/demo value — and makes the pattern wiki-forge
  documented (`agent-card-shape-on-mcp-hub`, from our own docs) literally true on the hub.
- **Design seed:** per [`mem/leveraging_multi_agent_frameworks.md`](mem/leveraging_multi_agent_frameworks.md) §3 —
  map the agents row (id, description, D16 skills) into the A2A card shape; no DB change, no
  new tool (peers can fetch over HTTP; add a tool only if dogfood asks).
- **Tests/docs:** shape + 404 on unknown agent; `specs.md` REST section, README interop note,
  D-number.

### E2 — Dashboard Mermaid session tracer — **M**
- **Why:** `session_id`/`parent_id` already encode the task→clarification→result DAG; the
  dashboard shows it as a flat list. A rendered graph (memo §2) is the single biggest
  legibility win left in workstream 2, incl. visual loop warnings.
- **Design seed:** render on demand (per-session modal or tab), fed from existing
  `/api/state` data; live-update via the D38 SSE push. **Check CSP first** — the D18/XSS
  hardening must admit Mermaid.js (bundle it or extend the policy deliberately, not ad hoc).
- **Dependencies:** none hard; counts toward workstream 2's open list (send/requeue from UI,
  search, richer metrics remain separate). **Tests/docs:** render check in the browser E2E
  pass; D-number.

### E3 — Git-native task helpers — **S**
- **Why:** make coding delegation concrete: payloads carrying commit refs, results carrying
  patches (memo §1).
- **Design seed:** a `scripts/` client-side utility wrapping `git diff`/rev-parse into
  payload/`context` conventions (e.g. `context="git-commit:<hash>"`), plus a documented
  convention in `connect-an-agent.md`. **Zero hub change** — pure convention + helper.
- **Gate:** first dogfood task that actually wants a patch round-trip. **Validation:** one
  live peer exchange applying a real diff.

### E4 — Structured permission delegation — **L (design M, build M/L)**
- **Why:** let a worker ask the initiating agent to approve a risky operation mid-task
  (memo §4; ACP `session/request_permission`).
- **Design seed:** a `kind="permission_request"` that parks like `input_request` on D17's
  machinery, with **typed options/outcome reusing B1's enum**. Trust caveat to state up
  front: with no auth (pre-F1), approval is advisory coordination between cooperating local
  agents, not a security boundary — consistent with the documented trust model.
- **Sequencing:** design consult with peers over the hub first (as pre-AHB-2), then build.
  **Dependencies:** B1 (outcome enum); benefits from F1 later. **Tests/docs:** park/unpark +
  outcome paths, `SKILL.md` handling rules, `specs.md`, D-number.

### F1 — `caller_id` / auth design spike — **M, doc-only**
- **Why:** every v1 decision (D11/D23) promises compatibility with a future authenticated
  caller model; nobody has written down what that model *is*. A design doc de-risks every
  track above without building anything.
- **Scope:** FastMCP `TokenVerifier` / static bearer options; token issuance (at register?
  operator-issued?); which tools enforce ownership; migration of free-text actor args
  (`agent_id`/`sender_id`) to the authenticated caller; what "who may broadcast" tightening
  looks like; keep the `127.0.0.1` bind regardless. Deliverable: a design memo + D-number,
  **no code**.
- **Gate for the build itself:** multi-user/networked lands on the actual roadmap.

### P1 — MCP `ContentBlock` payloads — **PARKED**
- v1's `str` payload is deliberate and reconfirmed (`mem/acp-evaluation.md` §Q3.4). If
  multimodal ever matters, MCP `ContentBlock` is the shape to adopt — not bespoke.
  **Trigger:** first real multimodal payload need.

### P2 — Auto-continue Stop/AfkStop hook variant — **PARKED**
- Beyond the peek-nudge (D19): auto-continue an agent's loop on pending work. The agy side is
  known-hostile (AHB-7: `stop_hook_active` always true; ambient nudge deferred).
  **Trigger:** the agy ambient nudge becomes worth chasing again, or a Claude-Code-only
  variant shows dogfood demand.

---

## Gates & triggers (the "don't build early" table)

| Item | Build when |
|------|-----------|
| D2 connection pooling | multi-user, or sustained real throughput pressure (round-2 baselines: 51 ops/s writer, ~70 calls/s HTTP) |
| P1 `ContentBlock` | first multimodal payload need |
| F1 auth **build** (spike is unGated) | multi-user/networked goes on the actual roadmap |
| P2 auto-continue hook | agy ambient nudge worth chasing again, or Claude-Code-only demand |
| C1 AHB-19 | next contested board run shows the gap again (else close wont-fix per AHB-17 #1 YAGNI) |
| E3 git helpers | first dogfood task wanting a patch round-trip |
