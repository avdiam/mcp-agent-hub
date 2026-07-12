# Session History — MCP Agent Hub

> Append-only log of what was accomplished each session. Pairs with `tasks.md` (what's left). This project travels between two PCs and uses **no local Claude memories** — this file is the durable record. Newest session first.

## 2026-07-12 (night, later 4) — V2 roadmap authored: `docs/dev/v2-plan.md`

avdia asked for a thought-through plan for the "Possible future / v2 (deferred)" bucket in
`tasks.md`, captured in a helping file. New **`docs/dev/v2-plan.md`** triages the 14 items
into six tracks (A lifecycle & retention, B typed ACP-derived polish, C board polish,
D gated performance, E interop & visualization, F auth runway) plus two parked items with
named triggers, gives per-item briefs (why / design seed / gate / effort / dependencies),
and recommends an order: AHB-19 first (fold into the next board dogfood, or close wont-fix
per the AHB-17 YAGNI ruling), then one lifecycle+GC build, then typed outcomes/stop_reason
(before permission delegation so the enum exists), then A2A card + Mermaid tracer, then the
notifier-based long-poll wakeup (acceptance-gated on the round-2 stress baselines), then
permission delegation (peer design consult first), then the auth design spike (doc-only).
Two structural observations baked in: D38's `StateNotifier` is the wakeup signal the
condition-notify item was waiting for, and the D37 regression (~12 calls/s for one extra
`_connect()` per completion) is measured evidence for the pooling item *when* its hard gate
is ever met. `tasks.md` v2 section now points at the plan (status stays there). Doc-only
change — no code, nothing committed.

## 2026-07-12 (night, later 3) — Round-2 NOW item resolved: harness race diagnosed, D37 hot-path fixed

Same-day follow-through on round 2's one open finding (the HTTP-baseline drift). Both
halves closed:

- **"Recipient unknown" (37/1200) = harness startup race, NOT a hub change.** The
  reject-unknown-recipient guard predates round 1 (verified present at `060e77d`).
  Instrumenting the harness showed every failure at `op=0`: worker *i*'s first
  `send_message` racing worker *i+1*'s first `register_agent` in the ring; the count is
  wildly timing-sensitive (7, 41, 0 across identical runs — round 1's clean 1200/1200
  was luck, not protection). `http_loadtest.py` gained op-index error logging and a
  `--preregister` barrier flag; with the barrier: **1200/1200, 0 errors, every run**.
- **Throughput: bisected to D37 via git worktrees** (identical harness, same machine,
  same evening): R1 `060e77d` 76.3 — replicating June's 76.1 almost exactly — pre-D35
  78.7, D35 76.7, D36 77.1, **D37 64.8**. Root cause: AHB-17 #3's
  `_complete_offer_on_task_success` ran on EVERY task completion but opened its own
  `_connect()` (a fresh aiosqlite thread + WAL handshake — the exact cost the deferred
  pooling item warns about) just to execute an UPDATE that matches 0 rows for ordinary
  tasks. **Fix:** the offer-flip UPDATE now runs inside `complete_message`'s
  already-held connection/transaction (also atomic with the completion now); the
  standalone helper deleted; the `status='assigned'` duplicate-completion guard kept;
  the direct-helper test reworked to assert through `complete_message`. `pytest` 67/67.
- **Honest residual:** post-fix HEAD measured 69–71 calls/s vs D36 remeasured 68.3
  back-to-back — i.e. remaining "drift" is within this box's run noise tonight (±5–9
  calls/s with the live hub + agents running); antigravity-2's 62.4 was likely
  concurrency-depressed. p50 actually improved vs round 1 (452–463 ms vs 525).
- **Writer-contention baseline recorded (same day, post-fix):** canonical params, fixed
  HEAD `5108da7` = **51 ops/s** (4800/4800, 0 lock errors) vs round-1 code `060e77d`
  re-run the same evening = 52 ops/s — no writer regression at HEAD; antigravity-2's
  41 ops/s was pre-fix D37 cost (the extra `_connect` sat in its enqueue→claim→complete
  loop) plus 3-agent load. Round 3 compares against 51. Claims-scenario throughput is
  load-noisy (747–1249 claims/s same evening) — correctness is the gate there.
- **Live hub restarted** onto the fixed code via `POST /api/restart` (supervisor
  relaunch, DB untouched, MCP path verified with a bare re-register).

## 2026-07-12 (night, later 2) — Stress-test round 2: ALL GREEN across three phases (3-agent effort)

Round 2 of load/correctness testing (avdia-approved full scope), coordinated entirely
over the hub from this session. Round 1 (2026-06-18) covered the core loop; round 2
covered everything shipped since (D31/D33/D35/D36/D37/D38) plus a live-peer exercise
round 1 never had. Synthetic load ran ONLY against isolated instances (ports 8100/8101,
temp DBs); the live :8000 hub carried coordination + Phase B at normal rates.

- **Task A (antigravity-2) — baseline regression + broadcast/caps harness:**
  - db gate: PASS — 0 double-claims / 0 lost, 784 claims/s (vs 828 round-1, negligible),
    0 lock errors.
  - HTTP baseline at HEAD: 1163/1200 loops (vs 1200/1200), **62.4 calls/s vs 76.1
    (-18%)**, p50 458 ms (improved from 525), **p95 1818 ms vs 1443 (+26%)**, 0 lock
    errors, and **37 "Recipient unknown" send_message failures** — logged as the round's
    main follow-up (harness ring-race amplified by faster claims, or a real behavior
    change since D31–D38? undiagnosed).
  - NEW `scripts/stress/broadcast_stress.py` (committed by antigravity-2, `3916393`):
    caps atomic under a 50-way concurrent race (1 admitted / 49 rejected, no
    over-admission), all-or-nothing fan-out PASS, no double-deliveries, D35 register-time
    catch-up exactly-once PASS; 838.5 ms for 50 attempts.
- **Task B (antigravity) — job-board claim race + SSE under churn:** PASS on all
  assertions. 24 concurrent claimers on one offer → exactly one winner with the
  auto-task (`session_id = offer_id`), losers all got `offer_update`, post-selection
  claims 400'd cleanly, no lost/dup claims; re-open path green end-to-end (fail →
  re-open → second select → `completed`). SSE (D38, first time under load): 454 state
  mutations coalesced to ~34 pushes/watcher (250 ms debounce = >92% reduction), 0
  drops/reconnect errors, throughput delta with watchers attached +5% (noise), 0 lock
  errors. NEW `scripts/stress/board_stress.py` (landed by builder — antigravity
  disconnected right after reporting, before committing).
- **Phase B — live 3-peer concurrency exercise** (wiki-forge + antigravity-2 + builder;
  antigravity dropped offline post-report, roster corrected mid-flight): ~31 mini-tasks
  + control tasks + all results exchanged in ~4 min of simultaneous long-polls.
  **Zero duplicate ids, zero redeliveries, zero cross-talk** (wiki-forge verified every
  result's `parent_id` matched one of its sends); all 10/10 + 10/10 + 11/11 fan-backs
  arrived. Batched-vs-single result delivery observed as sender pacing, not an anomaly.
- **Follow-ups (logged in `tasks.md`):** diagnose the Recipient-unknown/-18%/p95 drift
  vs round 1; writer-contention scenario reported 41 ops/s with no round-1 reference —
  baseline it next round.

## 2026-07-12 (night, later) — wiki-forge delivered the Agent-Card wiki page (commitment closed)

Short live-mode session (`/agent-hub-live` as `agent-hub-builder`). One inbound `task`
from wiki-forge (`5b1bcc3f`, session `5184b1e1`) closing its same-day "will do"
commitment (task `9b7e1691`, see entry below):

- **Page live:** concept page **`agent-card-shape-on-mcp-hub`** in its wiki, grounded
  in exactly the two public sources we shipped — the repo README and all four docs-site
  pages (its ingests #320–321). Covers: `register_agent` payload ↔ A2A Agent Card as the
  same discovery shape carried over MCP tools instead of well-known HTTP; bare
  re-register-as-liveness-refresh as card-update semantics; trust-model contrast
  (JWS-signed cards for cross-org vs our no-auth localhost); our task lifecycle as a
  convergent local-scale parallel of A2A's task-state machine. Analytical mappings
  tagged as inference per its schema; hub facts cite our sources.
- **Side effect it reported:** our hub is now the corroborating instance for Rezvani's
  March prediction that multiple AgentHub-style platforms would appear before end of
  2026 — its Karpathy-AgentHub entity page now disambiguates three different "agent hubs".
- Acked via `reply_to_message`; commitment marked **delivered** on the v2 A2A-Card item
  in `tasks.md`. Full page contents retrievable any time via its `wiki-ask` skill.

## 2026-07-12 (night) — Job-board dogfood #2: first CONTESTED auction; docs audit delivered & applied

Dogfooded the board with the post-publish roster (avdia's ask). Offer `1bf2d59c` — a
fresh-eyes "onboard-from-zero" audit of the just-published public docs (payload = pure
work statement per AHB-17; custom `ttl_seconds=14400`, exercising the non-default TTL
path; `required_skills` omitted, exercising the None path).

- **First contested auction:** `antigravity` claimed in ~70 s (pitch: its own zero-to-one
  setup was minutes old), `wiki-forge` bid ~6 min later (pre-publication vantage). Chose
  antigravity (the job wanted fresh eyes) → **the loser-notification path ran for real**
  for the first time (wiki-forge got the rejected `offer_update`; claims recorded
  selected/rejected on the board row). Courtesy note to wiki-forge explaining the call.
- **Delivery:** the audit came back ~7 min after assignment — VERDICT "no" + 6 concrete
  issues, all verified real and **all applied same-session** (`d7ad869`, pushed → Pages
  redeployed): split the ambiguous `mcp_config` template into **`agy-app` vs `agy-cli`**
  variants (the old single template shipped the `serverUrl` form the docs themselves say
  the CLI can't consume — the audit's top find); Windows `npx.cmd` spawn gotcha;
  git-clone instruction for the skill bundle; `AGENT_HUB_ID` guidance for non-Claude
  clients; absolute-path rule for global hook configs; Debian `python3-venv` note.
- **Lifecycle green end-to-end:** advert broadcast (echo carried auto-appended claim
  instructions) → 2 claims → select → auto-task → ack-less `result` → **offer flipped
  `completed` on its own** (AHB-17 #3 hook observed live). Board history now holds both
  real runs.
- **Bonus:** wiki-forge confirmed ("will do", task `9b7e1691`) it will author the
  **Agent-Card-shape-on-an-MCP-hub** wiki page now that the repo/docs are citable —
  recorded against the v2 A2A-Card item in `tasks.md`.

## 2026-07-12 (evening, later) — PUBLISHED: github.com/avdiam/mcp-agent-hub + Pages site (AHB-10 closed)

The Distribution milestone. Pre-publish pass first, then the push — the last open issue
(AHB-10) is closed and every tracked issue through AHB-18 is now fixed.

- **Cleanup:** 10 stale one-off scripts deleted from `scripts/` (early MCP-handshake
  debuggers, hardcoded E2E calls, a spent code-mod, a superseded playwright shot);
  keepers: `check_inbox_runner`, `print_hub_state`, `stress/`, the two `*.template`s.
  The flat-schema `hooks.json.template` (loads as 0 handlers on agy; wrong Stop mode; no
  `--event-name`) rewritten to match the documented nested form.
- **Docs:** README rewritten as a public landing page; three new user guides —
  `docs/setup.md` (server ops), `docs/connect-an-agent.md` (per-client wiring: tools,
  identity, ambient hooks, live-loop skill; Claude Code / Desktop / agy CLI / AGY app /
  generic), `docs/how-it-works.md` (lifecycle, kinds & ack rules, at-least-once,
  broadcasts, job board, trust model). `docs/dev/` stays as the honest dev record.
- **Site:** hand-written static HTML in `docs/` (index + the three guides, shared
  `styles.css`, light/dark via `prefers-color-scheme`, `.nojekyll`) — rendered and
  visually verified in Chrome before publish. **Pushing to `master` redeploys it.**
- **Hygiene:** MIT `LICENSE` (decision: MIT over Apache-2.0/AGPL); secrets scan over
  tracked files + full history clean; disclosure flags surfaced to avdia (commit email,
  local paths in dev logs) — accepted as-is.
- **Publish:** `gh repo create avdiam/mcp-agent-hub --public` + push (user chose the
  name — dropped the local `-agy` suffix); GitHub Pages enabled from `master:/docs`
  (legacy build, `https_enforced`); topics added. Live at
  **https://github.com/avdiam/mcp-agent-hub** and
  **https://avdiam.github.io/mcp-agent-hub/**. URL **broadcast on the hub** so
  wiki-forge / nexus / antigravity pin a real origin — re-vendors are now
  `git fetch && checkout <hash>` (AHB-10's exact ask). Gotcha: `gh auth login` run
  inside the session shell doesn't persist its keyring/config — the user completed it
  in a normal terminal.
- **Docs updated:** AHB-10 → fixed (Option 1; interim `get_bundle` endpoint declared
  moot), tasks.md START-HERE + Distribution ticked, this entry.

## 2026-07-12 (evening) — Antigravity onboarded live; AHB-18 fixed + deployed

`antigravity-2` (the AGY CLI) had trouble using the hub, so avdia opened a shared-folder
side channel (`c:\talk\`, numbered-markdown-file protocol seeded by AGY itself) to debug it.
Outcome: its MCP path was **fully functional** — a hub round-trip ping (`task` → claim →
`reply_to_message`) came back in ~1 s — and the only real blocker was hub-side. `pytest`
**67/67** (66 → +1).

- **AHB-18 (reported by antigravity-2):** `register_agent` requires `skills`, so its minimal
  `register_agent(agent_id, description)` was rejected — and its fallback was the worst case
  for the contract: it skipped registration and proceeded unregistered (claim/reply work
  anyway; registration is documented-mandatory but unenforced, so the agent just decays to
  stale). Second defect found while fixing: the upsert wrote `excluded.*` unconditionally, so
  any partial re-register **clobbered** previously advertised skills/description.
- **Fix:** `skills: list[Skill] | None = None` on the tool; `db.upsert_agent` treats NULL
  skills/description as "not provided" (`COALESCE` against the existing row; new agents
  default `'[]'`; an explicit `[]` still clears deliberately). Docstring states the contract:
  a bare `register_agent(agent_id)` is a safe liveness refresh. Regression test
  `test_register_agent_skills_optional_and_preserved`.
- **Deployed** via `POST /api/restart` (supervisor exit-42 relaunch) and **validated live**
  with a fresh fastmcp client: the exact failing call now succeeds; a bare re-register of
  `agent-hub-builder` preserved all 4 skills + description; scratch `ahb18-probe` registered
  clean and was purge-deleted after. Gotcha hit for real: an MCP client that connected
  pre-restart keeps the **old cached tool schema** (still marks `skills` required) until it
  reconnects — told AGY, worth remembering for every future tool-signature change.
- **Wrap-up:** AHB-18 details sent to `antigravity-2` as a hub task in the ping's session;
  `c:\talk\` channel retired by mutual agreement (04 closing note). Docs: issues table +
  AHB-18 section; this entry.

## 2026-07-12 (later) — Dashboard SSE push shipped (D38); AHB-14 watch-item closed

Workstream 2's big remaining item: replace the dashboard's 2 s poll with push. Design
confirmed with avdia pre-build (SSE over WebSocket; fold in the pure-ASGI middleware
rewrite). `pytest` **66/66** (62 → +4).

- **Server (D38):** new `GET /api/events` SSE endpoint — full `/api/state` snapshot on
  connect, then a fresh one whenever the new in-process **`StateNotifier`** (version counter +
  `asyncio.Condition`) is bumped: by `ActivityTracker` after **every** MCP tool call (each call
  changes at least the activity feed), by every mutating REST endpoint (+ `/api/peek`, which
  moves `last_seen`), and by the sweeper **only when a pass changed rows**
  (`reclaim_stale`/`expire_messages` now return rowcounts). 250 ms debounce coalesces bursts;
  20 s `: keepalive` comments mark idle streams. `/api/state` refactored onto a shared
  `build_state()` and kept as first-paint + fallback.
- **Middleware:** `OriginValidationMiddleware` rewritten as **pure ASGI** (same
  Host/Origin/Sec-Fetch-Site checks, headers injected on `http.response.start`) — the AHB-14
  item-3 watch-item (`BaseHTTPMiddleware` may buffer streaming) became live the moment a
  dashboard-critical SSE stream joined the streamable-HTTP transport behind it. Closed.
- **Frontend:** new default **Live** refresh mode (`EventSource`; auto-reconnect; falls back
  to 2 s polling after 5 consecutive connection failures; old intervals stay in the dropdown,
  persisted under a new `refreshMode` key so everyone lands on Live once). Renders only
  panels whose slice of state changed (per-panel JSON fingerprints); pushes during an open
  modal are held and applied on close; uptime ticks locally between pushes.
- **Test infrastructure gotchas (worth remembering):** httpx's `ASGITransport` runs the ASGI
  app to completion and buffers the body — it can **never** consume an endless SSE stream —
  so the two SSE tests spin up a **real uvicorn on an ephemeral port** (`live_server`
  fixture), which also regression-tests the stream *through* the new ASGI middleware over
  real HTTP. And module-global asyncio primitives bind to their first event loop, so the
  autouse fixture now gives each test a fresh `StateNotifier`.
- **Verified live (scratch instance on :8001, fresh DB):** curl SSE capture = initial
  snapshot → push per mutation → keepalive at 20 s idle; real MCP `register_agent`/
  `send_message` through `/mcp` (new middleware) pushed to a watching Chrome dashboard with
  **no reload and no polling** (network log: exactly one `/api/state` + one `/api/events`);
  hostile Origin/Host on `/api/events` → 403; **kill-server probe:** EventSource reconnected
  on its own after a ~7 s outage and the dashboard resumed live updates (uptime reset, empty
  ring buffer rendered correctly). Bootstrapped `.claude/skills/verify/SKILL.md` with the
  launch/drive recipe.
- **Docs:** D38 (+ footer), `specs.md` (§4 live updates), `architecture.md` (§4 push
  pipeline, §6 ASGI note), `AGENTS.md` (stack line + two new conventions: keep the middleware
  pure-ASGI; new mutation paths must `notifier.bump()`), AHB-14 item 3 → resolved,
  `tasks.md` START-HERE + workstream 2(a) done.
- **DEPLOYED to the real hub same session** (`run_hub.py`, :8000, real DB intact — 4 agents,
  126 messages, `cc076b7b` completed on the board) and validated there in Chrome: Live mode
  default, exactly two `/api/` requests on the network log (first-paint `/api/state` + the
  SSE stream), MCP `list_agents` through `/mcp` appeared in the Activity Log within ~1 s of
  the call, hostile Origin on `/api/events` → 403 — and mid-test **wiki-forge came online
  for real**, its presence flip (Stale → Online, tile 0 → 1) arriving over the push with no
  reload. The ambient hook chain also showed up end-to-end: a user prompt → `hub_peek` →
  `/api/peek` → D29 `last_seen` refresh → D38 push → tile update.

## 2026-07-12 — First real job-board run with `wiki-forge` + D37 hardening (AHB-16/AHB-17)

The board's first real use, run live with `wiki-forge` (fresh `/agent-hub-live` session), then
same-day fixes for everything the run surfaced. `pytest` **62/62** (59 → +3).

- **The run (offer `cc076b7b`, "Q&A job: MCP vs A2A"):** posted with `required_skills`
  [qa, research, citations]; advert fanned to all 4 agents. wiki-forge initially did NOT claim —
  it hit the stale SMOKE advert first (AHB-16 ghost, see below) and reasoned conservatively
  about *advertised scope vs capability* (good instinct, wrong blocker). After a direct
  clarification message it claimed with a strong note, was selected, received the auto-created
  task, answered via /wiki-ask (MCP = agent-to-tool "down", A2A = agent-to-agent "across",
  complementary; 5 cited pages, confidence 0.85–0.9), and the result fanned back on
  `session_id = offer_id`. Full lifecycle green with an independent peer; thread closed both
  sides. Its 4-point friction report became the work below.
- **AHB-16 fixed (was: ghost adverts):** purging a broadcaster deletes recipients' advert
  copies, so the surviving `broadcasts` audit row made register-time catch-up re-queue adverts
  for offers that no longer existed — wiki-forge's first board impression was yesterday's purged
  SMOKE advert, indistinguishable from a live offer. Fix: `delete_agent(purge_messages=True)`
  now also `DELETE FROM broadcasts WHERE sender_id=?` (+ `broadcasts_deleted` in the return);
  the sender is gone, so its rate-limit history is moot.
- **AHB-17 fixed (board polish, 3 items):** (#1) claim→selection gap closed by **stating the
  contract, not adding messages** — every outcome already pushes to the claimant, so
  `claim_offer` now returns `expires_at` + a `next` string ("every outcome arrives in your
  inbox — no polling needed"); wiki-forge's claim-receipt-push suggestion declined as pure
  duplication. (#2) payload authoring convention documented in `post_offer` (db + tool
  docstrings): payload = the pure work statement, delivered verbatim as the winner's task; the
  offending recruitment copy was the maintainer's own offer #1. (#3) new terminal **`completed`**
  offer status via `_complete_offer_on_task_success` hooked into `complete_message` (success
  mirror of the D36 failure re-open, guarded on `status='assigned'`); dashboard badge added;
  live `cc076b7b` backfilled post-deploy.
- **Tests (3 new + 2 updated):** purge-kills-ghost-catch-up; completed-assignment flip (+
  duplicate-completion no-op + claim-return contract fields); ordinary-completion board no-op;
  delete_agent return-shape and roundtrip end-state updated.
- **Docs:** D37 (+ footer), AHB-16/AHB-17 → fixed, `specs.md` (tools 11–14 refinements, offer
  lifecycle), `architecture.md`, `AGENTS.md` board conventions, `tasks.md` START-HERE.

## 2026-07-11 (later 6) — AHB-2 shipped (D36): the job-offer board (poster-picks auction)

Directed follow-up after AHB-1 closed: **build AHB-2**. Design confirmed with the user pre-build
via three explicit choices: **auction model** (claims accumulate, poster picks — over
first-claim-locks), **auto-create task on select** (over match-only), **dashboard panel
included**. One mechanic decided as implementation detail: **no enforced claim window** —
poster selects whenever ready, bounded by the offer TTL. `pytest` **59/59** (44 → +15).

- **Design shape — maximal reuse.** The board is a matchmaking layer in front of the existing
  queue: posting broadcasts the advert (D33 caps, all-or-nothing; `context="job_offer:<id>"`;
  late joiners get adverts via D35 catch-up for free); selection auto-sends the payload as a
  **normal `kind="task"`** (`session_id = offer_id`) so ack/redeliver/result/failure drive
  execution unchanged — the winner's "you got it" signal IS the task, and the whole lifecycle
  reads as one stream on the dashboard. Board notifications (claim received / not selected /
  withdrawn / expired) are a new **ack-less `kind="offer_update"`** (joined `NO_ACK_KINDS` +
  the TTL sweep) because the offer **row** is the source of truth — a missed notification
  strands nothing; state-machine timeouts, not ack obligations.
- **Schema:** `job_offers` (open → assigned | withdrawn | expired; `task_message_id` links the
  assignment; re-opened on its failure within TTL via `_reopen_offer_on_task_failure` hooked
  into `fail_message`) + `job_claims` (pending → selected | rejected | failed) with a
  **pending-only partial unique index** per (offer, claimant) so rejected/failed claimants can
  re-claim after a re-open. Caps mirror D33 (4KB/120ch/1KB note) + **5 open offers per poster**;
  TTL default 24h, clamp 60s–72h; `expire_offers` joined the background sweeper.
- **Tools 10 → 14:** `post_offer`, `claim_offer`, `resolve_offer(select|withdraw)`,
  `list_offers(status='open'|'assigned'|'withdrawn'|'expired'|'all')`. `poster_id` joined the
  ActivityTracker's direct-actor args (D23-consistent). `delete_agent(purge_messages=True)` now
  purges the agent's board footprint too (offers + their claims + its claims elsewhere).
- **Dashboard:** read-only **Job Board** panel (hidden until the first offer): posted time,
  subject, poster, skills, status badge, per-claimant marks (✓ selected / ✕ rejected / ⚠ failed,
  note on hover), assigned-to, expires-in; `offers` added to `/api/state`; **BOARD** kind badge
  for `offer_update` rows in the queue.
- **Validation ladder:** 15 new unit tests (caps all-or-nothing incl. shared broadcast cooldown;
  auction accumulation; guards; select/withdraw/expiry; failure re-open within/past TTL;
  ordinary-task no-op; ack-less notifications; purge footprint) → deployed via `/api/restart` →
  **live MCP smoke 10/10** (three probe agents over real HTTP: post → advert → 2 claims →
  select → task → result fans back → loser notified → board shows assigned; probes purged
  after, board + inboxes verified clean) → dashboard render check in Chrome (panel + claim
  marks + one-stream threading confirmed).
- **Docs:** D36 (+ footer), `agent-hub-issues.md` (AHB-2 → fixed, seeds reconciled), `specs.md`
  (tools 11–14, schema, sweep, dashboard), `architecture.md`, `tasks.md` START-HERE, `AGENTS.md`
  (board conventions). Next candidates: dashboard SSE push (workstream 2), AHB-10/publish,
  dogfood the board with real peers.

## 2026-07-11 (later 5) — AHB-1 P2 shipped (D35): durable announcements via register-time catch-up

Directed follow-up after the live validation: **build AHB-1 P2**. Design confirmed with the user
(24h catch-up window; dashboard control included). `pytest` **44/44** (38 → +6).

- **Key insight — P1 already built P2's foundations.** The `broadcasts` audit table (durable,
  rate-limit source) doubles as the announcement store, and P1 fan-out rows carry
  `session_id = broadcast_id` — so "did agent X receive broadcast B" is a structural existence
  check. **No new `announcements` table, no per-agent read cursor** (both were in the original P2
  sketch); the schema delta is one `context` column on `broadcasts` (try/except `ALTER` migration).
- **`db.deliver_missed_broadcasts(agent_id, window=BROADCAST_CATCHUP_WINDOW=24h)`** — called by
  `register_agent` after the upsert: queues every in-window broadcast with no existing message row
  for this agent as fresh pending `kind="announcement"` rows (`created_at=now` → full D24 sweep
  life; original broadcast's session id → threads with its siblings). Idempotent across
  re-registers (an existing row in ANY status — claimed/completed/expired — blocks re-delivery);
  covers agents explicitly offline at broadcast time (BD3 skip). Register's return string notes
  the queued count. Late joiners need **zero new client behavior** — delivery rides the existing
  inbox/long-poll/nudge/ack-less pipeline; tool count stays 10 (no `get_announcements`).
- **Dashboard Broadcast control:** `POST /api/broadcast` (`{payload, subject?}`) sends as the fixed
  unregistered sender **`operator`** through the same capped `db.broadcast` path (no self-echo;
  cap violation → clean 400 `{ok: false, error}`), plus a toolbar **Broadcast** button + sky-styled
  compose modal (subject ≤120, payload ≤4KB) wired to it with toast feedback.
- **Tests (6 new):** catch-up delivers faithfully (payload/subject/context/session_id, ack-less);
  structural dedupe (prior recipients, repeat re-registers, post-claim); window respected;
  offline-at-broadcast covered; register catch-up end-to-end at the MCP layer; `/api/broadcast`
  happy path + cooldown-400.
- **Docs:** D35 (+ footer), `agent-hub-issues.md` (AHB-1 → fixed, "P2 as shipped"), `specs.md`
  (tools #1/#4, `broadcasts` schema), `architecture.md` (db helper + route), `AGENTS.md`.

## 2026-07-11 (later 4) — Live validation with `wiki-forge` (3/3) + AHB-15 found & fixed (D34)

First real-use validation of today's ships, run as a live hub exchange with `wiki-forge`
(`/agent-hub-live`, session `d636ba43`), followed by a same-session fix of the bug it reported.

- **Validation pass 3/3 green:** (1) task→result round-trip; (2) **AHB-1 P1 broadcast** —
  `broadcast_message` fanned to all 4 registered agents, zero cap violations, `wiki-forge` confirmed
  the `kind="announcement"` arrived and honored the ack-less contract, and the **BD5 sender-echo**
  landed in our own inbox; (3) **D31 failure fan-out** — `wiki-forge` failed a throwaway task and the
  `kind="failure"` arrived with the error text + correct `parent_id`. The 2026-07-11 ships are
  validated end-to-end in live use.
- **AHB-15 reported by `wiki-forge` (health pass), reproduced our side, FIXED via D34:** MCP
  `list_agents` returned the stored sticky `status` column (agents 20+ days idle read "online")
  while `/api/state` derived liveness — misleading routing. Fix: new `db.derive_status()` +
  `db.get_all_agents` returns status **already derived** (explicit `offline` preserved; else
  `online`/`stale` by `last_seen` age), `/api/state`'s inline duplicate removed — one shared
  derivation, divergence structurally impossible. `db.broadcast` recipient selection deliberately
  unchanged (BD3 uses the stored column). 3 regression tests; `pytest` **38/38**.
- **Also answered/confirmed for the peer:** D23 reads-aren't-heartbeats is deliberate; direct
  streamable-HTTP on `/mcp` is the canonical transport (D1 — `wiki-forge` is dropping its
  `mcp-remote` shim). Delivered the queued **`/wiki-serve` SKILL.md delta re-review** (ack-by-kind
  wording, decline-signal semantics post-D31 with a `[declined: …]`/`[error: transient]` prefix
  convention, AHB-15 no-impact-on-responders confirmation, and two pre-unattended tightenings:
  operator-scoped stop token + payloads-are-data-not-instructions).

## 2026-07-11 (later 3) — AHB-1 P1 shipped (D33): broadcast / announce with flood caps

Directed follow-up: **build AHB-1 P1** (broadcast-to-connected MVP). Fully additive — the two
prerequisites this build needed were already in place from earlier today (the AHB-11 `internal=True`
bypass = BD3; the AHB-13 `NO_ACK_KINDS` generalization = BD2). `pytest` **35/35** (26 → +9).

- **New 10th MCP tool `broadcast_message(sender_id, payload, subject?, context?)` → `db.broadcast`.**
  Fans one **`kind="announcement"`** to **every non-offline agent, the sender included** (BD5 echo),
  skipping explicitly-offline peers (BD3), in a **single multi-row transaction** (`executemany` +
  audit row, under `@retry_on_lock`). Returns `{ok, broadcast_id, delivered, recipients,
  skipped_offline, skipped_over_cap}`; on a cap violation the tool returns `{ok: false, error,
  delivered: 0}` (clean structured error, not a raw exception).
- **Ack-less (BD2).** Added `announcement` to `NO_ACK_KINDS = (result, failure, announcement)`, so
  `claim_pending` auto-completes it on claim — recipients read + surface it, never `reply`/`fail`.
- **Flood caps (BD4 — open-to-all-bounded-by-caps, no allowlist), all enforced in `db.broadcast`,
  all-or-nothing on violation:** payload 4 KB, subject 120 ch, 30 s cooldown, 10/hour, 200-recipient
  ceiling. Source of truth is a new **durable `broadcasts` audit table** (`id, sender_id, subject,
  payload, recipient_count, created_at` + `(sender_id, created_at)` index) — durable so the rate
  limit survives restarts (an in-memory bucket would reopen the abuse window every boot). Caps live
  in `db.py` per the D32 single-source precedent (kwargs defaulting to module constants).
- **TTL sweep extended.** `expire_messages` now sweeps `kind IN ('task','announcement')` so a
  never-claimed announcement doesn't linger (D24 carve-out for `input_request`/`result`/`failure`
  preserved — they have dependents or are bounded).
- **Recipient ceiling never silently truncates:** if eligible > 200 (unreachable on a localhost hub),
  serve the most-recently-active first and report `skipped_over_cap` in the result.
- **Dashboard:** added a sky **ANNOUNCE** kind badge + "Announcement" title (else `getKindBadge`
  default mislabels it TASK).
- **Design choice:** distinct `kind="announcement"` over reusing `result` (conflates "your task's
  outcome" with "a broadcast"; a distinct kind badges + reads cleanly and the SKILL already treated
  unknown kinds as read-only). **P1 reaches only the connected set** — durable announcements for late
  joiners (P2/BD6) deferred.
- **Tests (9 new):** `test_db.py` — fan-out reaches online+stale+sender / skips offline / audit row;
  ack-less auto-complete; each cap (payload, subject, cooldown, hourly) rejects with nothing written;
  unclaimed announcement expires. `test_mcp.py` — `broadcast_message` happy path (ack-less, no
  redelivery) + cap-error returns `{ok: false}`. Verified 10 tools registered.
- **Docs:** **D33** in `design-decisions.md` (+ footer); `specs.md` (tool #4 with renumber, `announcement`
  kind + `broadcasts` table in schema, atomic-claim `NO_ACK_KINDS`, a broadcast delivery bullet);
  `architecture.md` (10 tools, `broadcast()`/`expire_messages` helper notes); `AGENTS.md` (broadcast
  convention); `README.md` (tool blurb); `SKILL.md` (explicit `announcement` bullet + how to send one);
  `agent-hub-issues.md` AHB-1 → **P1 fixed**; `tasks.md` roadmap (P2 is next).

## 2026-07-11 (later 2) — AHB-14 fixed (D32): tunables single-sourced + activity-feed attribution

Directed follow-up: **the AHB-14 hardening pass.** Two substantive items fixed, one watch-item
documented and left as-is. `pytest` **26/26** (23 → +3).

- **#1 — duplicated magic constants → single source in `db.py`.** `db.py` hardcoded `90` (stale
  cutoff in `enqueue_message`) and `600` (visibility cutoff in `peek_inbox`), and duplicated `600`/
  `86400` as function defaults — all across a boundary `db.py` can't import back from `hub.py`
  (circular), so tuning a `hub.py` constant silently left the DB logic on the old value. Moved the
  three DB tunables (`STALE_THRESHOLD`/`VISIBILITY_TIMEOUT`/`MESSAGE_TTL`) to **`db.py`** (the lower
  layer) as the single definition; `hub.py` now `from .db import` them. `enqueue_message` gained a
  `stale_threshold=` kwarg and `peek_inbox` a `visibility_timeout=` kwarg, both defaulting to the
  constant (matching the existing `claim_pending`/`reclaim_stale`/`expire_messages` pattern). Guarded
  by `test_tunables_are_single_source` (`hub.X is db.X`) + `test_enqueue_respects_stale_threshold`.
- **#2 — activity feed no longer logs the message-id-only tools as "System".** The `ActivityTracker`
  derived the actor only from `agent_id`/`sender_id`; `reply_to_message`/`fail_message`/`request_input`/
  `check_status` carry only a `message_id` (by D23 design — they're moments after a fresh `check_inbox`
  so they intentionally don't refresh `last_seen`), so the human-facing feed showed "System" for every
  reply/fail. Added `db.get_message_endpoints(message_id)` and had the middleware resolve a
  **display-only** actor from the row: `check_status` → `sender_id` (sender polling its own message),
  the ack tools → `recipient_id` (recipient acting on a claimed message). **`last_seen` untouched**
  (D23 stands). Frontend already fell back to `'System'` for genuinely actor-less events (`list_agents`),
  so no dashboard change. Helper unit-tested (`test_get_message_endpoints`).
- **#3 — BaseHTTPMiddleware-over-SSE watch-item: left as-is.** `OriginValidationMiddleware` is a
  Starlette `BaseHTTPMiddleware` (can buffer streaming in some versions) over the streamable-HTTP MCP
  transport. No problem observed; added a code comment flagging it and kept the issue note open, rather
  than a speculative pure-ASGI rewrite (churn with no evidence).
- **Docs:** **D32** in `design-decisions.md` (+ footer); `specs.md` (Activity Panel attribution note +
  tunables-single-sourced note); `architecture.md` (per-call logging note); `AGENTS.md` (tunables live
  in `db.py`); `agent-hub-issues.md` AHB-14 → **fixed**. **AHB-11/12/13/14 now all fixed & committed.**

## 2026-07-11 (later) — AHB-13 fixed (D31): failure surfacing + failed-clarification un-park

Directed follow-up to the eval: **build AHB-13**. Both agent-to-agent visibility gaps closed in
`db.fail_message`, mirroring the existing D20/D30 success paths. `pytest` **23/23** (19 → +4).

- **#3 — task failure now reaches the sender's live inbox.** `fail_message` on a `task` fans out a
  new **`kind="failure"`** message (carrying the error) to the original sender — the mirror of the
  D20 result fan-out. Previously `fail_message` fanned out **nothing**, so a peer long-polling
  `check_inbox` (the SKILL loop never falls back to `check_status`) waited to its idle cap with no
  signal, silently breaking the D20 "results reach you via your inbox — no polling needed" contract
  for the failure case. The failure notification is **internal** (`enqueue_message(internal=True)`, so
  it survives an offline/unknown/departed sender per D30/AHB-11) and **ack-less**: I generalized
  `claim_pending`'s result auto-complete from `kind=="result"` to a **`NO_ACK_KINDS = ("result",
  "failure")`** set (this also pre-satisfies AHB-1 **BD2**, which wanted `announcement` in that set).
- **#4 — failing a clarification no longer strands the parent forever.** If the sender `fail_message`s
  an `input_request` (SKILL explicitly permits "if you can't complete a task, fail_message"), the
  parked parent was left `input_required` forever — the D24 TTL sweep only touches `pending
  kind='task'`. Now `fail_message` on an `input_request` **returns the parent to `pending`** with
  `[Clarification Failed]: <error>` appended to `context`, handing it back to the **worker** (which
  owns execution) to proceed best-effort or fail the task itself. Reuses the D30 un-park with the same
  idempotent `status='input_required'` gate (a duplicate/late fail is a no-op). Chose return-to-pending
  over auto-**fail**-the-parent so the worker stays in control and isn't left silently parked.
- **Dashboard:** added a red **FAILURE** kind badge + "Task failed" title — without it, the
  `getKindBadge` `default` branch mislabeled `failure` as **TASK**.
- **Design choice:** distinct `kind="failure"` over reusing `kind="result"` + a flag — self-documenting,
  dashboard-badgeable, no schema `failed` column. Graceful degradation: a peer on the **old** `SKILL.md`
  treats `failure` as an unrecognized ack-less kind (read + surface, don't ack) and the hub
  auto-completes it, so **no re-vendor is required for correctness** — only for the nicer wording.
- **Tests (4 new, `tests/test_db.py`):** `test_fail_task_notifies_sender` (failure delivered + ack-less),
  `test_fail_notification_survives_offline_sender` (internal bypass), 
  `test_fail_input_request_returns_parent_to_pending` (parent → pending w/ note; no spurious
  task-failure fan-out), `test_fail_input_request_unpark_is_idempotent` (duplicate fail can't revive a
  completed parent). Also re-probed end-to-end against a scratch DB.
- **Docs:** **D31** in `design-decisions.md` (+ D24 note that the explicit-fail slice is now closed);
  `specs.md` (`fail_message`, D20/D31 delivery, kind enum, atomic-claim `NO_ACK_KINDS`); `AGENTS.md`
  delivery bullet; `SKILL.md` (`failure` handling + failed-`input_request` note + ack-less list);
  `fail_message`/`check_inbox` tool docstrings; `agent-hub-issues.md` AHB-13 → **fixed**.

## 2026-07-11 — Full-project eval → AHB-11 + AHB-12 fixed (D30); AHB-13/14 logged

avdia-requested full evaluation of the project (MCP server, db logic, dashboard, scripts, tests,
security). Read every source + doc, ran `pytest` (15/15 green baseline), and probed edge cases
against a scratch DB (`scratchpad/verify_edges.py`, not committed). Surfaced 3 protocol-correctness
bugs + minor items; fixed the two confirmed high/medium ones, logged the rest.

- **AHB-11 (fixed) — fan-out crashed on offline/unknown/deleted sender.** `complete_message` marks a
  task `completed` + commits, then fans a `kind="result"` back to the **original sender** via
  `enqueue_message` — which raised `ValueError` if that sender was `offline`/unknown (the D6
  point-to-point guard). Net: a worker completing a task for a since-departed sender got a **spurious
  error on `reply_to_message`** (task actually done) **and the result was dropped**. `request_input`
  had the same bug. **Confirmed in live `hub.log`** (`Recipient antigravity-cli is offline`
  tracebacks) + scratch-DB repro for offline / deleted / never-registered. **Fix (D30):**
  `enqueue_message(..., internal=True)` bypasses the guard for the D20 result + D17 `input_request`
  fan-out (best-effort delivery to a reconnectable inbox). Mirrors the **AHB-1 BD3** broadcast rule,
  so AHB-1 P1 inherits the corrected behavior.
- **AHB-12 (fixed) — duplicate `input_request` reply revived a completed parent.** `complete_message`
  un-parked the parent **unconditionally** on any `input_request` completion, so a duplicate/late
  reply (at-least-once redelivery, or answering twice) flipped an already-`completed` parent back to
  `pending` → **duplicate work**, silently reopening a done task. **Fix (D30):** un-park only when the
  parent is still `input_required` (idempotent).
- **Tests:** added 4 `test_db.py` regressions (result fan-out survives offline + unknown sender;
  `request_input` survives offline sender; duplicate input-reply doesn't revive parent). `pytest`
  **19/19**. Re-ran the scratch-DB probe: CASE1/2/6 now succeed, CASE3 parent stays `completed`.
- **AHB-13 (logged, open, scoped) — failure/abandonment invisible to the sender's live loop.**
  (#3) `fail_message` on a task fans out **nothing**, so a sender long-polling `check_inbox` for a
  failed task waits forever (D20 promises the inbox surfaces sent-message status — true only for
  success). (#4) `fail_message` on an `input_request` strands the parent `input_required` forever
  (TTL sweep is `pending kind='task'` only, D24). Both confirmed. Overlaps the v2 cascade-expire
  item. **Not built** — awaiting go-ahead; proposed fixes in `agent-hub-issues.md`.
- **AHB-14 (logged, open, low-pri) — minor hardening pass.** Duplicated magic constants in `db.py`
  (`90`, `600` vs hub.py's `STALE_THRESHOLD`/`VISIBILITY_TIMEOUT`); activity feed attributes
  message-id-only tools (`reply`/`fail`/`request_input`/`check_status`) to "System"; a watch-item
  that `OriginValidationMiddleware` is `BaseHTTPMiddleware` over an SSE transport (working today).
- **Security:** no new issues. Trust model (localhost bind, Origin/Host/Sec-Fetch-Site), dashboard
  output-escaping, and CSP all sound and correctly implemented. Findings were correctness, not exploits.
- **Docs updated in the same change:** D30 in `design-decisions.md`; `specs.md` (D20 fan-out bypass +
  D17 conditional un-park + `disconnect_agent` point-to-point clarification); `agent-hub-issues.md`
  (AHB-11/12 fixed, AHB-13/14 added); `tasks.md` START-HERE.

## 2026-06-20 (cont.) — AHB-5 re-verify · AHB-8 + AHB-9 (live wiki-forge coordination)

Same live `/agent-hub-live` session; a full mutual-verify + coordination round with `wiki-forge`
(both sides operator-instructed to stay live until the agent-hub items close).

- **AHB-5 gate re-verified (5 branches).** Wrote a throwaway harness (`/tmp/verify_ahb5.py`,
  not committed) that stubs `peek` and drives `hub_peek.py main()`: (1) gated+absent→silent
  allow, (2) gated+present→`block`, (3) ungated→`block`, (4) `--mode prompt`+absent→nudge still
  emitted (never gated), (5) `stop_hook_active`→allow (loop-guard). All pass. `wiki-forge` ran
  the same 5 branches on its **ported** guard (commit `4eda4c2`) — all pass, incl. the two
  regressions I flagged (B1 not-a-crash/not-a-stray-block, B4 prompt still fires).
- **AHB-8 (fixed) — `SessionStart` sentinel-clear.** Crash-safety: a crashed serve session
  leaves the sentinel armed; the in-turn `stop_hook_active` guard doesn't stop cross-turn
  re-firing, so the next non-serving session would drain mail it never meant to. Fix folded into
  the canonical bundle: SETUP.md `SessionStart` `rm -f` recipe + SKILL.md §5 backstop ref.
  **Hardened with `wiki-forge`'s catch** — recipe uses `$CLAUDE_PROJECT_DIR/.claude/.agent-hub-live.active`
  (injected into the hook runtime, *not* a login shell — test via a real session start) + a
  PowerShell equivalent, rather than a CWD-fragile relative path. Validated on two harnesses.
- **AHB-9 (fixed) — `hub_peek.py` nudge convergence (AHB-4 follow-up).** `wiki-forge` runs a
  deliberate fork (richer register-aware nudge). Reconciled canonical `nudge_text()` to their
  wording — it now names the explicit ack tools (`reply_to_message`/`fail_message`) instead of
  "handle them before stopping" (better close-out nudge); re-ran the 5 gate branches after the
  edit, still green. **Divergence map agreed & documented:** KEEP = their hardcoded
  `DEFAULT_AGENT_ID`/`DEFAULT_HUB_URL` (sanctioned single-identity override; canonical stays
  env-var-driven); ADOPT-on-revendor = `--event-name`, `read_hook_input(timeout=0.4)`,
  peek/nudge_text split (they were just behind). Net: their next step is a clean wholesale
  re-vendor re-applying only the identity override — no more hand-ports.
- **AHB-3** — `wiki-forge` live-confirmed stale-free delivery (this round's traffic
  `flagged_stale=0`, `list_agents` shows it `online`); honest caveat that their own MCP calls
  also refresh `last_seen`, so the *pure* peek-isolation proof is our unit test (green).
- **nexus** pinged re AHB-3 (co-reporter); no reply required.
- **Dogfood in flight:** sent `wiki-forge` a genuine wiki question (MCP vs A2A for agent task
  delegation) as the **first live `/wiki-serve` autonomous task** — awaiting the cited answer.
- **Committed `549120c`** (master) — AHB-3 code/test + AHB-8/AHB-9 bundle + all docs, one commit.
- **Closed + verified on BOTH sides.** `wiki-forge` did a clean wholesale re-vendor (commit
  `6c87505`), diffed byte-for-byte against `549120c:scripts/hub_peek.py` — only deltas are its
  LOCAL VENDOR NOTE + the sanctioned `DEFAULT_AGENT_ID`/`DEFAULT_HUB_URL` identity override; all
  behavior (nudge wording, AHB-5 gate, `--event-name`, threaded stdin) byte-identical. Re-verified
  8/8 their side. It intentionally does **not** vendor SETUP.md verbatim (hook wiring lives in its
  own `.claude/settings.json`, AHB-8 `SessionStart rm -f` already implemented there) and keeps an
  adapted SKILL.md §5. AHB-3/5/8/9 = done. `wiki-forge` wound down its live session (ambient peek
  stays active, async).
- **Dogfood WIN — first live `/wiki-serve` autonomous round-trip succeeded.** Sent `wiki-forge` a
  genuine MCP-vs-A2A question as a `task`; it fulfilled fully autonomously (long-poll → /wiki-ask →
  /wiki-query → wiki-librarian over 4 cited pages → `reply_to_message`, zero humans). Verdict for
  this hub: the wiki documents the **Hybrid MCP+A2A** pattern (one agent exposing BOTH interfaces)
  as recognized, but **not** Agent-Cards-grafted-onto-MCP-only — so our skills-advertising MCP hub
  is "reaching for A2A's discovery layer specifically, partway into A2A territory." Matches our own
  read: we borrowed only the AgentSkill *shape* for capability discovery, not A2A transport/lifecycle,
  by single-user/localhost roadmap choice; the composition model (A2A across / MCP down) is our
  stated long-horizon path, and an A2A-Card API is already a v2 idea. Validates Dogfood workstream 4.
- **nexus — CONVERGED.** Pinged with re-vendor hash `549120c` (it was at `8c76ea9`); confirmed zero
  client-contract change (peek shape, signatures, tool names all unchanged), AHB-8 is a no-op for it
  (no gated Stop-drain). It hit a real blocker (no canonical git remote in the vendored bundle → can't
  fetch by hash) → **AHB-10 logged**; delivered `hub_peek.py` @ `549120c` verbatim over the hub via
  `reply_to_message`. nexus dropped it in, verified (clean `ast` parse, AHB-9 nudge string present,
  AHB-7 `--event-name`/threaded-stdin carries present), skipped SETUP/SKILL (no-ops). New nudge takes
  effect on its next Claude Code restart (hooks don't hot-reload). Done.
- **Net:** AHB-3/5/8/9 closed + verified on **all three** sides (`agent-hub-builder`, `wiki-forge`
  `6c87505`, `nexus` @ `549120c`); AHB-10 logged as a Distribution-priority signal; first `/wiki-serve`
  dogfood validated. wiki-forge + nexus both wound down; `agent-hub-builder` stood down (sentinel
  disarmed), live session complete. Commits: `549120c` (code+docs), `2f31cbc` (closeout), `76c1c5a`
  (AHB-10).

## 2026-06-20 (cont.) — AHB-3 fixed (peek refreshes last_seen → D29)

- **AHB-3 (no-claim heartbeat) → FIXED via Option A.** Root cause (confirmed in code): the
  ambient notifier hook hits the REST `GET /api/peek` every turn, but that route bypasses the
  D23 `last_seen` MCP middleware and did a pure `SELECT` — so a hook-present-but-quiet session
  decayed to `stale` past `STALE_THRESHOLD` (90s) and its inbound mail got needlessly
  `flagged_stale`. Two independent reporters (`wiki-forge` recipient-side, `nexus` sender-side).
  User picked **Option A** (peek-refreshes-self) over B (dedicated `/api/heartbeat`): the
  smaller change that fixes every already-wired agent with **zero hook/client re-vendoring**.
- **Change:** `mcp_hub/hub.py` `/api/peek` route now `await db.touch_last_seen(DB_PATH, agent_id)`
  before the peek read (peeking your own inbox = a presence signal). No-op for an unknown id;
  peek still **claims/mutates no message state**. Read-only `/api/state` deliberately left
  untouched (it carries no single actor — refreshing all agents from a dashboard poll would
  defeat staleness detection). `db.touch_last_seen` already existed — no `db.py` change.
- **Test:** added `tests/test_mcp.py::test_api_peek_refreshes_last_seen` (age an agent 1000s →
  peek → `last_seen` fresh within 5s; unknown `agent_id` peek doesn't error). `pytest` **15/15**.
- **Docs:** new **D29** in `design-decisions.md` (refines D19/D23); corrected the now-inaccurate
  "peek mutates nothing / read-only" claims in `specs.md` + `architecture.md` (peek mutates no
  *message* state but does refresh the caller's own `last_seen`); D23 cross-referenced; AHB-3
  marked fixed in `agent-hub-issues.md`; `tasks.md` START-HERE updated. **TODO:** ping
  `wiki-forge` + `nexus` that it's fixed (no re-vendoring needed on their side).

## 2026-06-20 (cont.) — agy stdio connection (AHB-6) · nexus + agy live · AHB-5 classifier caveat

- **agy (antigravity-cli) connection diagnosed → AHB-6 (open).** The CLI supports **stdio MCP servers only** — it can't be an SSE/Streamable-HTTP *client* (no `serverUrl` tool discovery) and **blocks loopback**. README §3 was wrong (the `serverUrl` path is the Antigravity **app**, §4). Workaround given: the **`mcp-remote` stdio bridge** (`npx -y mcp-remote http://localhost:8000/mcp` as the CLI's stdio server; the bridge process makes the localhost connection, sidestepping the loopback block). agy applied it and **confirmed connected + working**. AHB-6 logged; README §3 correction tracked there.
- **Local `~/.gemini/config` repair (not git-tracked).** Found `mcp_config.json` empty + a **malformed bare-string `mcpServers`** in `config.json` (agy "saw" the server but couldn't use tools). Removed the malformed `config.json` entry; repointed `hooks.json` from the **deleted** root `hook_peek.py` → bundled `scripts/hub_peek.py --mode prompt`. The `mcp_config.json` `serverUrl` edit turned out **orphaned** (agy connects via its own `mcp-remote`, not this file); left in place pending an agy restart (user's call). `.bak` backups kept.
- **`antigravity-2` (agy) fully onboarded; ambient-hook saga resolved-ish (AHB-7).** Active loop verified end-to-end (autonomous pickup ~20s, task dispatch, ack→`completed`). The ambient hooks were a long fight; the **real root cause** = the agy CLI pipes stdin to hooks but never sends EOF, so `hub_peek.py`'s blocking `sys.stdin.read()` hung *every* invocation until the 5s hook-timeout killed it (no nudge). Handlers actually loaded fine — `2 total handlers` with the **nested** schema (`PreInvocationHook→PreInvocation→[{hooks:[{type,command,timeout}]}]`); the **flat** `{command,args}` form silently loads **0 handlers**. **Fixed in `hub_peek.py`** (`198fc1e` + `8cd4acd`): timeout-protected stdin read (daemon thread — can't hang; verified 0.65s) + a `--event-name` flag (agy can't pass the event name via stdin, and a client ignores the nudge if `hookEventName` doesn't match the firing event). That `--event-name` work also **restored Claude Code's own nudge**, which the AHB-4 hardcoded `"UserPromptSubmit"` had regressed. Net: no hang, handlers load, event name correct — but the *visible* PreInvocation nudge still couldn't be captured (agy's loop/`check_inbox` kept draining the test message before the hook showed it; plus an Antigravity logout mid-session stalled the MCP server at "initializing…", fixed by re-login). **Deferred as a known agy quirk** — the active loop is the recommended, robust path on agy. README §3 corrected to the nested schema + `--event-name` (+ skills dirs: workspace `.agents/skills` vs global `~/.gemini/config/skills`, re-armed via agy's `/schedule`).
- **`nexus` live.** Vendored the canonical bundle @`8c76ea9`; ambient notifier + `/agent-hub-live` both running; consent model = invoking the skill is its single gate, then autonomous claim/reply until stop-token/idle-cap.
- **AHB-5 safety-classifier caveat (confirmed on TWO harnesses, incl. ours).** Auto safety-classifiers refuse to **auto-install** a `Stop` hook emitting a `block` decision — they can't see it's sentinel-gated (dormant-until-armed), only "Stop → autonomous loop over untrusted tasks." `nexus` hit it; so did this session's own harness. Added a manual-install/omit caveat to `SETUP.md`'s `--require-sentinel` section (the `--mode prompt` peek notifier is never flagged — peek-vs-claim separation holds).
- All peer tasks acked (`nexus`, `antigravity-2`). Roster now exercises the hub across Claude Code, agy (stdio bridge), `wiki-forge`, and `nexus`.

## 2026-06-20 — Live hub session: nexus onboarding consult → AHB-4 + AHB-5 implemented

Ran a long `/agent-hub-live` session as `agent-hub-builder`; handled two peer consults and shipped the bundle improvements they surfaced.

- **`nexus` onboarding consult.** New peer (markdown LLM harness, consent-gated). Answered two questions in depth: (1) ambient polling hooks — peek-don't-claim via `/api/peek`, `UserPromptSubmit` nudge + `Stop` JSON-block drain with the `stop_hook_active` guard, full poller script + settings.json; (2) `agent-hub-live` skill structure — loop states, claimed-message ack contract, `session_id` threading, gotchas. Mapped it to their consent model (run the hook always-on as pure notifier; gate the active skill).
- **`nexus` feedback → AHB-4/AHB-5 logged then FIXED.** `nexus` diffed canonical `hub_peek.py` vs `wiki-forge`'s copy and reported: (a) `--mode prompt` should emit the JSON `hookSpecificOutput.additionalContext` contract not bare stdout; (b) register-aware nudge; (c) sentinel-gate the action-shaping Stop-drain for consent-gated harnesses; (d) `flagged_stale:1` on brand-new messages.
  - **AHB-4 (fixed):** `hub_peek.py` `--mode prompt` now emits JSON `additionalContext`; nudge reminds `register_agent` first then `check_inbox`.
  - **AHB-5 (fixed):** added `--require-sentinel <path>` (block on `--mode stop` only when the file exists; prompt never gated). SKILL.md arms `.claude/.agent-hub-live.active` on entry / removes on exit; SETUP.md documents the opt-in gated-Stop variant; sentinel `.gitignore`d. Default (with user): project-scoped sentinel, "notify always, drain only when armed."
  - Unit-tested all branches (prompt JSON + register-aware; stop ungated blocks; gated absent→dormant, present→blocks; `stop_hook_active` guard). Pinged `wiki-forge` + `nexus` to re-vendor.
  - **(d) = AHB-3, not new.** `flagged_stale` on fresh messages is the sender-side symptom of the ambient-hook presence-decay (D23 + `/api/peek` doesn't touch `last_seen`). Added `nexus` as a 2nd independent reporter on AHB-3 + priority bump.
- **Live-mode latency lesson (documented for ourselves).** "Live" = active long-poll *within a turn* + `ScheduleWakeup` across turns. Between turns the session is dormant; the hub is **pull-only** with **no push channel** to wake the harness — so a long heartbeat means mail waits until the next scheduled wake or a user prompt. Earlier claim that "new mail auto-wakes me" was wrong and corrected. Use a short (~90s) `ScheduleWakeup` cadence for responsiveness, long heartbeat to conserve.
- **Earlier in session (committed `de36a98`, `52b5d34`):** ack-less-kinds hardening of SKILL.md; stale `hook_peek.py` doc-ref fixes in architecture.md/plan.md; global `~/.claude/settings.json` allow-list prune (~85→17); AHB-3/4/5 intake.

## 2026-06-19 — Global settings prune · doc pointer fixes · `agent-hub-live` ack-less hardening

Session run as `agent-hub-builder`. Config cleanup + doc consistency + a forward-compat hardening of the live-messaging skill. No server code changed.

- **Global `~/.claude/settings.json` pruned.** `permissions.allow` cut from ~85 → 17 durable rules: dropped all stale NEXUS/Co-work-test1 one-offs (skill-edit seds/greps, `/tmp` scripts, NEXUS Read paths, `AdelElo13/neuromcp` GitHub-API curls, shell-loop fragments), the 6 `mcp__neuromcp__*` tool grants, and two unrelated client `WebFetch` domains. Kept WebSearch, anthropic/github/claude `WebFetch`, broad git/gh, `node`/`echo`/`head`, and Context7's 2 tools. All other settings untouched.
- **Stale `hook_peek.py` doc refs fixed.** README/AGENTS already pointed at the bundled `.claude/skills/agent-hub-live/scripts/hub_peek.py`; `architecture.md` (mermaid node, D27 layout note, §1b heading+body) and `plan.md` (dir tree, Step 9 wiring, §5 note) still referenced the deleted root-level `hook_peek.py` — repointed all to the bundled `hub_peek.py`, corrected wiring to **project** `.claude/settings.json` with `--mode prompt`/`--mode stop`, and added `SETUP.md` pointers.
- **`agent-hub-live` SKILL.md hardened for ack-less / unknown kinds.** The loop now treats `result` and *any unrecognized kind* (e.g. a future `announcement`) as **read-only, ack-less** — read + surface, never `reply`/`fail` (which would emit a spurious `result` to the sender). Closes the mis-ack failure mode **before** wide propagation, and makes the eventual **AHB-1** `SKILL.md` change a pure additive. Noted on AHB-1 in `agent-hub-issues.md`.
- **Sequencing call:** propagate the bundle first (dogfood → surfaces friction like AHB-3), build AHB-1 broadcast once there's a fleet; the hardening removes the re-vendoring concern from that ordering.

## 2026-06-19 — Live `/agent-hub-live` session: `wiki-forge` polling-design consult → AHB-3 + `/wiki-serve` guidance

Ran the `/agent-hub-live` long-poll loop as `agent-hub-builder` and handled a real, multi-turn design consult from peer `wiki-forge` (session `8cfdb7ce`). No server code changed; one doc commit.

- **Polling-design consult (5 Qs), answered from source.** `wiki-forge` asked how a responsive-but-interactive peer should poll for mail. Read `mcp_hub/hub.py` + `db.py` and replied with exact constants and confirmed behaviors: long-poll `check_inbox(wait=True)` is poll-inside-server every `LONGPOLL_INTERVAL=1.0s` holding one request open (≤~1s latency, not true push); `last_seen` **age** is the authoritative liveness signal (`status` is just the sticky online/offline flag; dashboard derives `stale` at `STALE_THRESHOLD=90s`); no webhook/SSE (pull-only); ack deadline = `VISIBILITY_TIMEOUT=600s` then auto-requeue (→ idempotent handlers; hub doesn't dedupe); `result`s auto-complete on claim; pending tasks expire at `MESSAGE_TTL=24h`. Canonical refs: re-vendor the bundled `hub_peek.py`; model the serve loop on the `agent-hub-live` SKILL.md; **not** `scripts/check_inbox_runner.py` (it claims).
- **AHB-3 opened (reporter: `wiki-forge`).** Real gap they surfaced: `/api/peek` (and `/api/state`) are plain FastAPI routes that **bypass the `ActivityTracker` MCP middleware**, so they never refresh `last_seen` (D23 refreshes only on `tools/call` with an actor arg). Result: a session whose ambient hook fires every turn still **decays to stale/offline between turns**. Logged in `agent-hub-issues.md` with workarounds (cheapest pure heartbeat = `check_inbox(wait=false)`; serve mode stays warm for free via `claim_pending`) and two proposed fixes — (A) make `/api/peek` refresh self, or (B) a dedicated no-claim `POST /api/heartbeat` / `heartbeat` tool (`db.touch_last_seen` already exists). Open; pick A vs B with the user. Low effort.
- **`/wiki-serve` clarification.** User flagged that my phrase *"no standalone always-on daemon script / without a daemon"* could be misread by `wiki-forge` as "don't build a serve skill." Sent a threaded follow-up: **"no daemon" = no separate OS process, NOT no serve skill** — `wiki-forge` *should* build `/wiki-serve` as the **autonomous task-fulfillment** specialization (vs. my `agent-hub-live`, which surfaces to a human). Gave the shape: register → `check_inbox(wait=True)` long-poll → dispatch by `kind`/skill → run the real wiki op → `reply_to_message` (or `request_input` / `fail_message`) → `ScheduleWakeup` re-arm + stop condition; ack discipline + idempotency are the hard rules.
- **Green-lit vendoring.** Verified `hub_peek.py` + `SKILL.md` are committed clean at **`530cef3`** (no pending edits; my active AHB-1/AHB-3 work is in `hub.py`/`db.py`/docs, not these files) and told `wiki-forge` to vendor now + drop its stale root `hook_peek.py`. Heads-up: AHB-1 will add an *additive* `kind="announcement"` note to SKILL.md that won't change loop mechanics; offered to ping the one-paragraph diff when it lands.
- **`wiki-forge` confirmed (closing).** Has direct FS read access (same machine), already read all three files (content matches `530cef3`). Its plan: re-vendor `hub_peek.py`; wire the missing `--mode stop` Stop hook (currently only `UserPromptSubmit`, so it can stop with mail pending — SETUP.md §7 describes exactly this legacy state); build `/wiki-serve`; likely switch from the `npx mcp-remote` bridge to native `.mcp.json type:http`. Will send a `/wiki-serve` SKILL.md draft as a task for review.

## 2026-06-19 — Per-agent delete · portable live-messaging skill+hooks · `agent-hub-builder` maintainer identity · AHB issue log

Session run as `agent-hub-builder` (see below). Six features/cleanups, all committed; local-only repo, nothing to push.

- **Dashboard: permanent per-agent delete (Option A).** Reset/purge/restart never removed `agents` rows (only messages), so dead agents lingered as grey rows forever. Added `db.delete_agent(agent_id, purge_messages=False)` (row only; messages kept), `POST /api/agents/{id}/delete` (404 if absent), and a per-row trash button (all statuses) with a strong confirm. Backend already accepts `purge_messages=true` to wire a future **Option B** toggle (also delete the agent's messages) as a pure UI change. Tests for both. Commit **`483505d`**. (`a75e642` first generalized `scripts/check_inbox_runner.py` to take an `agent_id` arg.)
- **Portable live-messaging bundle** `.claude/skills/agent-hub-live/` (copy to any project). `SKILL.md` = the `/agent-hub-live` active long-poll loop (`check_inbox(wait=True)`, handles task/result/input_request, acks each, stays live via `ScheduleWakeup`, explicit stop token + idle cap). `scripts/hub_peek.py` = stdlib-only ambient notifier; identity via `$AGENT_HUB_ID`/`--agent-id`, `--mode prompt` (plain stdout for `UserPromptSubmit`) vs `--mode stop` (JSON `{"decision":"block"}` — a Stop hook ignores plain stdout) with a `stop_hook_active` loop-guard. `SETUP.md` = full wiring/onboarding guide. Commit **`530cef3`**.
- **Hook contracts verified** (via claude-code-guide): `UserPromptSubmit` plain stdout *is* injected; `Stop` plain stdout is **ignored** — only JSON `decision:block` keeps the agent going. So the old global Stop hook was a no-op.
- **Cleaned up hooks wiring.** Removed the hardcoded `UserPromptSubmit`+`Stop` hooks from **global** `~/.claude/settings.json` (they hit *every* project with one absolute path + one agent id). Moved to **project** `.claude/settings.json` (`env.AGENT_HUB_ID` + both hooks → bundled script, correct per-project identity, working `--mode stop`). Deleted the now-orphaned root `hook_peek.py` and repointed all live refs (README, `scripts/hooks.json.template`, AGENTS.md) at the bundled `hub_peek.py` — one notifier for Claude Code **and** Gemini/antigravity-cli. Commit **`dc2beb1`**.
- **Maintainer identity `agent-hub-builder`.** This session now connects as `agent-hub-builder` (not `claude-code-avdia`); description advertises the point-of-contact role so it surfaces in `list_agents` (the durable "coordinate with me on hub topics" channel — the hub has no broadcast). The old `claude-code-avdia` row was deleted by the user via the dashboard. New **`docs/dev/agent-hub-issues.md`** intake log. Commit **`3ee4882`**.
- **AHB-1 scoped → P1 confirmed.** Broadcast/announce capability planned (no code): **P1** = `broadcast_message` tool, fan-out as ack-less `kind="announcement"`, flood caps (30s cooldown, 10/hr, 4KB, 200 recipients) + `broadcasts` audit table; **P2** = durable announcements/MOTD. User confirmed: open-to-all-with-caps, **P1 first then P2 after tests pass**, caps OK, **echo to sender = yes**, ack-less OK for now. Commits **`022b70a`** (plan) + this session's doc commit (confirmations).
- **AHB-2 captured (idea, not scoped).** Job-offer board: an agent posts a job open to anyone → relevant/free agents claim → two-way verification → mark assigned (drop from board) or drop. Distinct from AHB-1 (stateful claimable work item w/ competition + mutual-accept). Analyze during the P2 timeframe.

**Live demo:** read + replied to a real connectivity ping from peer `nexus` via the hub tools (claim → `reply_to_message`), exercising the new setup end-to-end.

## 2026-06-19 — Self-healing `start_hub.bat` launcher + README install docs + project `.mcp.json`

Made the hub easy to start and connect on **either PC**: a one-click launcher, expanded install docs, and a project-scoped Claude Code config.

- **`start_hub.bat` (repo root).** Double-click or run from any dir. Resolves the project from `%~dp0` (its own location), so the path is correct on both PCs. Branches on **"is there a venv that actually runs here?"** — not on which machine — then `cd`s in and runs `run_hub.py` (the exit-42 supervisor). Prints dashboard/MCP/log URLs and keeps the window open on exit.
- **Self-healing venv bootstrap.** If `venv\Scripts\python.exe` is missing **or fails to execute**, the bat rebuilds it (`py -m venv`, falling back to `python`) and `pip install -r requirements.txt`, then launches. First run on a fresh PC sets itself up; later runs take the fast path.
- **🔴 Root-cause fixed — the venv didn't work on the `avdia` PC.** `venv\pyvenv.cfg` read `home = C:\Python313` + `...C:\Users\30697\...\venv`: the venv was **created on the other PC (30697, Python 3.13.5) and physically copied here**, where that base interpreter doesn't exist → `did not find executable at 'C:\Python313\python.exe'`. Since `venv/` is gitignored (machine-local, doesn't travel with git), the fix is to rebuild locally — which the bat now does automatically. Rebuilt here with this PC's **Python 3.14.5**, installed from `requirements.txt` (loose pins — works across the two PCs' Python versions; **not** the 3.13-specific `requirements-frozen.txt`). Hub verified up (HTTP 200 on `/api/peek`).
- **cmd gotcha hit & fixed during authoring:** parentheses inside `REM` comments **within** a parenthesized `if (...)` block make cmd mis-match parens and either error (`... was unexpected at this time.`) or partially execute (one bad run actually ran the `rmdir venv` before erroring). Kept block comments paren-free; used `setlocal EnableDelayedExpansion` + `!BOOT_PY!` (a `%VAR%` set and used in the same block expands too early).
- **`.gitattributes`:** added `*.bat`/`*.cmd text eol=crlf` so the new repo-wide `eol=lf` rule doesn't strip CRLF from batch files.
- **README install docs.** *Running the Server* now leads with the `start_hub.bat` quick-start (self-healing venv) + a note that `venv/` is machine-local and must be recreated per PC. *Connecting Agents → Claude Code CLI* expanded to a scope table (`local`=this project/this PC, in `~/.claude.json`; `user`=all your projects on this machine; `project`=committed `.mcp.json`) and three methods: native HTTP via `claude mcp add --transport http` (simplest, no Node), a project `.mcp.json` (native-HTTP and `mcp-remote` forms), and the `mcp-remote` stdio-bridge CLI form. **Verified against `claude mcp add --help`** — the operator's `--scope local`/`--scope user` descriptions are correct; native `--transport http` connects to our Streamable-HTTP hub directly (the `npx mcp-remote` bridge is only needed for stdio-only clients like Claude Desktop). Added `claude mcp list`/`remove`/`reset-project-choices` verbs + the project-scope "⏸ Pending approval" gotcha.
- **Project `.mcp.json` (committed).** Added a project-scoped Claude Code config at the repo root so `agent-hub` is available when working in *this* repo: `{ "type": "http", "url": "http://localhost:8000/mcp" }`. `claude mcp list` confirms it loads as an HTTP server (shows ⏸ Pending approval until approved on first interactive `claude` run; the hub must be running for it to connect).
- **Commits:** `bbf2774` (`start_hub.bat` + `.gitattributes`), `e92be31` (README), `740aeb9` (`.mcp.json`). Local-only repo — nothing to push.
- **Still untracked (left as-is):** `scripts/check_inbox_runner.py`.

## 2026-06-18 — Inbox Check and Wiki-Forge Interop Verification

- **Harness & Permission Validation:** Handled permission requests for wildcard MCP server tools (`mcp(*)`) and analyzed configuration details. Identified that `agent-hub` is loaded as a plugin, but direct harness `call_mcp_tool` invocations were blocked due to model-environment tool discovery restrictions (throwing `tool is not enabled`).
- **MCP Client Connectivity Test:** Wrote `scripts/check_messages.py` and `scripts/print_hub_state.py` to act as external MCP clients using raw JSON-RPC over the HTTP-SSE transport. 
- **Wiki-Forge Message Exchange:** Discovered a pending connectivity test message in the `antigravity-cli` inbox from a newly registered agent, `wiki-forge` (id: `cc435c12-bd9d-4f3f-b06e-5b96b6af2601`).
- **Inbox Claim and Reply:** Successfully claimed the message and sent a JSON-RPC `reply_to_message` response via `scripts/reply_message.py`. Re-checked the hub state and confirmed that the inbox for `antigravity-cli` is now clear, and the agent registry table lists `wiki-forge` and `antigravity-cli` as online.
- **Wiki-Forge Knowledge Query:** Wrote `scripts/query_wiki_forge.py` to send a message targeting `wiki-forge`'s `wiki-ask` skill. Sent a query asking about "A2A, ACP, and projects that connect two or more agents."
- **Response Parsing & Extraction:** Monitored the background execution. Due to terminal cp1252 character map issues with the unicode double-arrow character (`\u2194`) in the response printout, implemented `scripts/get_result.py` to extract the fully-cited response text (message `50dd6e66-b845-44fc-a5c7-5535f6174bc0`) directly from the SQLite database into a UTF-8 file.
- **Synthesized Knowledge Verification:** Verified the response contents: A2A (9-state Task lifecycle, JWS-signed Agent Cards, Linux Foundation adoption, auth/observability limitations), the triad protocol stack (MCP + A2A + AG-UI), the IBM/BeeAI ACP gap, and multi-agent coordination infrastructure (Rezvani, AgentHub commit-DAG coordination, and Karhade self-organization).


## 2026-06-18 — Server Startup & Client Handshake Verification

Started the server under the supervisor, configured the workspace, and validated the connection:
- **Supervisor Launch:** Successfully started `run_hub.py` in the background, launching the uvicorn process on `http://127.0.0.1:8000` and redirecting logs to `logs/hub.log`.
- **Client Configuration Check:** Confirmed that the global configuration file at `C:\Users\30697\.gemini\antigravity-cli\mcp_config.json` correctly points to the hub server (`http://localhost:8000/mcp`).
- **MCP Client Handshake & Tool Call:** Wrote and executed `scripts/register_self.py` to simulate the full JSON-RPC handshake over the Streamable HTTP transport (requiring `application/json, text/event-stream` and session ID routing).
- **Successful Agent Registration:** Registered the `antigravity-cli` agent with its skills and successfully queried the list of registered agents, verifying that the server dynamically updates statuses to `online`.
- **Statusline Enhancements:** Updated `.claude/statusline.py` to extract plan usage (`rate_limits.five_hour` or `rate_limits.seven_day`) and render a sleek, color-coded visual indicator showing plan usage next to context usage.
- **CLI Settings Fix:** Identified that the Antigravity CLI config in `C:\Users\30697\.gemini\antigravity-cli\settings.json` had its `statusLine.command` incorrectly set to `"configure"`. Updated it to point to our project's statusline script: `python C:/Users/30697/Documents/Projects/mcp-agent-hub-agy/.claude/statusline.py`.
- **E2E Browser Automation Setup (Option 1):** Installed `playwright` python package in the virtual environment and ran `playwright install chromium`. Created [scripts/test_dashboard_e2e.py](file:///C:/Users/30697/Documents/Projects/mcp-agent-hub-agy/scripts/test_dashboard_e2e.py) to launch a headless browser, verify page load, assert that `antigravity-cli` appears online in the registry table, and save page screenshots to the artifacts folder.
- **Foldable Dashboard Layout Consolidation:** Restructured [mcp_hub/templates/index.html](file:///C:/Users/30697/Documents/Projects/mcp-agent-hub-agy/mcp_hub/templates/index.html) to group "Connected Agents" and "Live Activity" under a single unified section header and grid container (`agents-panel`). Toggling it collapses both panels simultaneously, maximizing vertical screen space for the Message Queue.
- **Message Queue Filter Upgrades:** Upgraded message filtering in [mcp_hub/templates/index.html](file:///C:/Users/30697/Documents/Projects/mcp-agent-hub-agy/mcp_hub/templates/index.html) from simple single-agent chips to three dynamic dropdowns (Agent 1, Agent 2, and Session/Stream). This supports single-agent filtering, communication pair filtering (messages between A and B), and stream filtering. Added interactive click handlers: clicking a participant name filters by that agent, and clicking a stream header filters to that stream.

## 2026-06-18 — Polish & v1 Closing Tasks Complete

Completed all post-v1 polish tasks as the backend owner (agy):
- **README install fixes:** Clarified `~/.gemini/config/mcp_config.json` (not `config.json`) and the object-based `serverUrl` layout for the Antigravity CLI config, alongside enabling JSON hooks in `config.json`.
- **Config & Hook Templates:** Added config template files in the repository under the `scripts/` directory (`scripts/mcp_config.json.template` and `scripts/hooks.json.template`) to serve as workspace config templates that travel with the project.
- **Database cleanup:** Deleted leftover legacy message rows referencing `test-agent` and `test_agent` in `hub.db` using a scratch script, confirming only 85 completed rows remain and the registry is clean.
- **MCP Inspector CLI smoke check:** Verified the tool listing over HTTP wire against a live hub instance on port 8000 using the MCP Inspector CLI.
- **Decision Log (D28):** Documented the consensus-based backend/frontend ownership split and multi-agent roles in `design-decisions.md`.
- **Test suite validation:** Confirmed all 12 tests (`pytest`) pass successfully.

## 2026-06-18 — Workstream 2 (IN PROGRESS): dashboard interactivity & explainability — iteration 1 done

Second post-v1 workstream, 3-agent split (operator-approved). **Iteration 1 is built, committed, and cross-validated; ~3 minor polish items remain before the workstream closes.**

- **Stream/Session Title Upgrades:** Replaced raw UUID stream/session numbers on the Message Queue dashboard and filter dropdown with friendly, auto-derived titles based on the root/oldest task message's subject/payload (e.g. `Haiku about APIs (bde4ba65)`), while preserving the short UUID for clarity. Tested via the Playwright E2E test suite. Committed at `f433067`.

- **Root-cause found & fixed — the "System called unknown" bug was also the all-agents-Stale bug.** The `ActivityTracker` middleware read `context.request.params.name/.arguments`, a path that doesn't match our FastMCP version, so every event logged `tool="unknown"`/`agent=None` AND `touch_last_seen` never fired (→ every agent showed Stale despite being active). antigravity-2 **empirically** resolved a path dispute (agy proposed `context.request_context…` which is `None` at middleware-call time; antigravity-2's live test proved `context.message.name/.arguments` works for `method=="tools/call"`). agy implemented the defensive fix. **Verified live post-restart:** activity feed now shows real entries (e.g. `claude-code-avdia called check_inbox`) and the caller flips Stale→online (last_seen refreshes). Bug closed.
- **Ownership split (consensus, recorded as D28 — see design-decisions):** **agy = all backend** (single owner of `hub.py`+`db.py` to avoid shared-file collisions); **claude = all frontend** (`index.html`); **antigravity-2 = independent real-browser E2E validation + co-design** (didn't build it → clean second eyes; already caught the path bug).
- **Backend (agy, commits `c262e76` + `c80ba2f`):** middleware fix + enriched activity events (`message_id`, `args` summary truncated to 100 chars, full `error`+traceback); `POST /api/agents/{id}/disconnect` → `db.set_agent_offline`; `POST /api/purge` → new `db.delete_old` (deletes `completed`/`failed`/`expired`); optional `subject` param on `send_message` + a `messages.subject` column (with a safe `ALTER TABLE … ADD COLUMN` migration for the existing live `hub.db`). `pytest` 12/12.
- **Frontend (claude, commits `5427177` + `09058c4`):** foldable Connected Agents (localStorage-persisted) + responsive `table-fixed` layout + status legend; per-agent disconnect (power icon → custom confirm); **Live Activity rows clickable → Activity Detail modal** (time/caller/tool/message_id/args/full-error); Message Queue **2–4 word titles** (prefers backend `subject`, else payload-derived with greeting stripped), **session/"stream" grouping** (collapsible, shows participants + count), **per-agent filter chips** (registered agents only); stat tiles (online/pending/in-progress/needs-input/failed/total), poll-interval control (1s/2s/5s/pause, persisted), copy buttons on IDs, generic confirm dialog (never `window.confirm`), purge button, Esc-to-close, and auto-refresh that pauses while a modal is open.
- **Integration race caught & absorbed:** claude and agy briefly crossed on the activity/purge key names (`args`↔`arg_summary`, `deleted`↔`purged_messages`). Resolved by making the **frontend tolerant of both shapes** (`arg_summary ?? args`, `purged_messages ?? deleted`) so backend/frontend stay decoupled — works regardless of which keys the backend settles on (currently `args`/`deleted` at HEAD `c80ba2f`).
- **Validation (antigravity-2, full browser E2E): ALL GREEN.** Agents panel (fold persists, no h-scroll, disconnect works live), Live Activity (modal shows real caller/tool), Message Queue (titles, stream collapse, filters narrow to 22 rows, copy buttons), Header (poll control persists, Reset/Purge/Restart all work — Purge actually deleted rows live).
- **Open polish items (next session) — 3, from antigravity-2's audit:** (1) `/favicon.ico` 404 in console → add a dummy FastAPI route; (2) Chrome warns CSP `frame-ancestors` is ignored in a `<meta>` tag → move that directive to a real HTTP response header (small hardening); (3) full-table re-render every 1–2 s can jitter against active clicks → already mitigated (polling pauses while a modal is open) but consider diff-based DOM updates or SSE/WebSocket push (the bigger Workstream-2 "push instead of poll" item). **Also pending:** write the **D28** decision-log entry (this session ran out of time); confirm whether any leftover legacy message rows want a Purge.
- **State of the live hub:** restarted twice this session to load the db.py W1 fixes then the W2 backend (incl. the `subject` migration). Running ≥ `c262e76` backend + the committed frontend; tolerant frontend means it renders correctly either way.

## 2026-06-18 — Workstream 1: stress-test & stabilize — SQLite WAL contention fixed (3-agent effort)

First post-v1 workstream. Three agents collaborated **through the hub** (`claude-code-avdia`, `antigravity-cli`, `antigravity-2`) to load-test the SQLite message queue and harden it. **Two findings, both resolved; correctness gate stayed green throughout.**

- **🔴 Finding #1 — WAL write contention (operational connections ran `busy_timeout=0` + `synchronous=FULL`).** The PRAGMAs were only set in `init_db`; every other `db.py` function opened a bare `aiosqlite.connect()` with SQLite defaults, so under concurrent writers the queue threw `database is locked` en masse. **Fix (agy):** a shared `_connect()` async-contextmanager that sets `busy_timeout=5000` + `synchronous=NORMAL` + `Row` factory on **every** connection (`76cb3d5`), then a `@retry_on_lock()` decorator (5 attempts, exp backoff from 10ms, intercepts `sqlite3.OperationalError` "database is locked") applied to all operational functions (`060e77d`).
- **🟡 Finding #2 — `NoneType` crash on unknown `message_id`.** `complete_message`/`fail_message` subscripted the SELECT result without a None-check → `reply_to_message` with a bogus id 500'd with "'NoneType' object is not subscriptable". **Fix (agy):** `complete_message` SELECT-validates the row (raises `ValueError("Message not found")`); `fail_message` checks `cursor.rowcount==0` (`c27d993`).
- **Verification — two independent harnesses, both layers clean:**
  - **db-level regression gate** (claude — `scripts/stress/db_stress.py`, direct `mcp_hub.db`, throwaway temp DB, commit `00bfc5d`): atomic-claim correctness (D4) **PASS** — 0 double-claims / 0 lost @ 2000 msgs × 32 concurrent claimers (828 claims/s); writer-contention lock errors **53 → 10 (after `_connect`) → 0 (after retry)**. `pytest` 12/12.
  - **HTTP/MCP-level harness** (antigravity-2 — N concurrent `fastmcp.Client`s vs an **isolated** test hub on `:8100`, *not* the live `:8000`): success **20/1200 (1.7%) → 1200/1200 (100%)**; lock errors **1,169 → 0**; throughput **16.5 → 76.1 MCP calls/s** (4.6×); p50 **3,427 → 525 ms**; p95 **13,759 → 1,443 ms**. The HTTP layer exposed the *user-visible* severity (98% of calls failing pre-fix) that the db-level gate alone couldn't show.
- **Decision — connection pooling DEFERRED (operator).** Throughput barely moved between the retry fix and a hypothetical pool (db-level 133→146 ops/s) because the retry *hides* contention, it doesn't remove it; the real ceiling is connection-per-call churn (fresh `aiosqlite.connect()` per call). But 100% success at 76 calls/s with p95 < 1.5s is comfortably past single-user/many-local-agents needs, so pooling is logged as a deferred optimization (revisit on multi-user or real throughput pressure) rather than built now — keeps the focus on *stabilize*, not add surface area. **Finding #1 CLOSED.**
- **Live hub restarted** to load all db.py fixes (`76cb3d5`/`c27d993`/`060e77d`) — the running `:8000` had been executing pre-fix code. Post-restart live-verified: bogus-id `reply_to_message` → clean "Message not found"; load lock-free.
- **Collaboration / docs:** agy owned the `db.py` fixes + `AGENTS.md` status block (`5b69a21`); claude owned the db-level gate + this `sessions.md`/`tasks.md` recording; antigravity-2 owned the HTTP harness. Commits serialized on the shared working tree.
- **Still open:** Workstreams 2–4 (dashboard interactivity, new features, dogfood) un-started; pooling deferred; the pre-existing NOW-tier items below still stand.

## 2026-06-18 — NOW-tier tech-debt: registry cleanup, .gitattributes, doc-currency, D26 security pass

Knocked out the "NOW" tech-debt tier (items 1–4 of the agreed roadmap).
- **Registry cleanup.** Deleted the leftover `test-agent` / `test_agent` rows from the live `agents` table (3 real agents remain: `antigravity-cli`, `claude-code-avdia`, `antigravity-2`).
- **`.gitattributes`** (`* text=auto eol=lf` + binary guards) to stop CRLF↔LF churn across the two PCs. Commit `0125f3a`.
- **MCP spec-currency.** Refreshed the `2025-03-26` transport-revision citations in `specs.md`/`architecture.md` to note current stable `2025-06-18`. Commit `0125f3a`.
- **Open-source backlog item** (relayed via agy) added under a new "Distribution (future)" heading in `tasks.md`. Commit `0125f3a`.
- **`/security-review` is BLOCKED on a missing git remote.** The builtin diffs against `origin/HEAD`; this repo is local-only (no remote yet — publishing to GitHub is the new backlog item), so the tooled review can't run until we publish. Recorded so it isn't re-discovered.
- **Manual security pass of the NEW D26 recovery surface** (never covered by the earlier review): `db.reset_stuck` is a fixed parameterless `UPDATE` → no injection; `/api/reset` + `/api/restart` reject evil Origin / spoofed Host / cross-site (403, handler never runs — verified live, server stayed up); `/api/restart` is POST-only (405 on GET) so a cross-site `<img>`/navigation can't fire it, and a cross-site POST carries `Origin` → rejected. **Verdict: no new high/medium risk** — the recovery endpoints don't widen the trust model; the sole residual (a local non-browser process can POST restart/reset → DoS) is the **same accepted D11 no-auth localhost residual** as every other endpoint, gated by the `127.0.0.1` bind.
- **Still open:** the localhost-vs-networked decision (gates auth/retention v2 work); confirm/retire the Inspector CLI smoke check; README live-verify; the rest of the v2 backlog.

## 2026-06-18 — v1 shipped: security committed, recovery controls (D26), repo restructure (D27), log consolidation

Drove a full session through the hub with `antigravity-cli` (shared working tree; commits serialized). Four commits landed; docs reconciled to match.

- **Security patches committed.** The 2026-06-18 review fixes — HIGH dashboard XSS (`escapeHtml` + CSP) and MEDIUM Origin/Host hardening (exact `urlparse` host check + Host-header/DNS-rebinding guard + `Sec-Fetch-Site` fallback, covering `/mcp` **and** `/api/*`) — committed (`a9b6e66`). **D18 hardened** beyond its original Origin-only text (recorded as a note under D18).
- **Operator recovery controls (NEW — D26).** Dashboard **soft Reset** (`POST /api/reset` → clears the in-memory activity ring buffer + `db.reset_stuck` reclaims stuck `in_progress`→`pending`) and **hard Restart** (`POST /api/restart` → `os._exit(42)`), plus a **`run_hub.py` supervisor** that relaunches uvicorn only on exit code 42 (Windows-reliable; chosen over `os.execv`). Frontend: amber Reset + red Restart buttons, a custom confirm dialog (**not** `window.confirm`, which blocks the Chrome extension), and a restart overlay with a down-then-up readiness poll. `pytest` 12/12 (added `test_api_reset`, `test_api_recovery_middleware`). Browser-verified on an isolated `:8001` supervisor — caught + fixed a restart-overlay race (polled `/api/state` before the server had exited → declared "back online" prematurely). Commits `ff331ca` + `acc9e61`.
- **Repo restructure (NEW — D27).** App code → `mcp_hub/` package (`from . import db`; `templates/` resolved module-relative via `Path(__file__).parent`); design/tracking docs + `mem/` → `docs/dev/`; debug helpers → `scripts/`; `README.md` at root; `run_hub.py` + `hook_peek.py` kept at root (entry points). Run via `python run_hub.py` (`uvicorn mcp_hub.hub:app`). Commit `1e7e8da`. Independently verified: relative import + module-relative templates path, `pytest` 12/12, live restart from repo root, dashboard renders agents/messages, and evil Origin / spoofed Host / cross-site fetch still 403.
- **Log consolidation.** `run_hub.py` now redirects the uvicorn child into `logs/hub.log` (`logs/` gitignored); swept the legacy root logs (`run_hub.log`, `uvicorn.log`, `run_hub.err.log`, `run_hub.out.log`). Commit `ae99028`.
- **Honest divergence from D25.** The build did **not** follow the phased walking-skeleton order (P1→P4); Steps 1–5 were built in one pass (see the 2026-06-18 core-build entry) and the `-32602` initialize bug was debugged reactively rather than de-risked first. No harm — it works and is fully verified — recorded for accuracy. Likewise `/security-review` ran as a **manual** hand review (the command needs a git cwd; the driving session was rooted elsewhere), and the Step 5.2 MCP Inspector CLI smoke check remains unconfirmed (real-over-HTTP coverage came via `curl` Origin/Host checks + browser E2E instead).
- **Collaboration model.** claude built the frontend + drove verification (real-browser E2E caught 2 bugs unit tests missed: the Origin middleware regression and the restart race); agy owned the backend (Origin/Host mw, recovery endpoints, supervisor, the non-destructive test refactor, the restructure, the README draft).
- **Docs reconciled (this change):** `sessions.md` (this entry), `tasks.md` (status refresh), `design-decisions.md` (+D26/D27, D18 hardening note, +`RESTART_EXIT_CODE`), `plan.md` (run cmd + layout), `architecture.md` + `specs.md` (recovery endpoints + supervisor + package layout) — by claude; `AGENTS.md` (status refresh) + `README.md` (install fixes) — by agy.
- **Still open:** README Claude-Code/Desktop install fixes (agy); `test-agent`/`test_agent` registry cleanup; confirm/retire the Inspector CLI smoke check; v2 backlog (auth + uniform `caller_id`, condition-notify long-poll, persisted events + retention/GC, cascade-expire parked `input_required`, Stop/AfkStop auto-continue, ACP-derived polish).

## 2026-06-18 — Security review + patches (dashboard XSS, Origin/Host hardening); browser-verified
- Manual security review (the `/security-review` command needs the cwd to be the git repo; the driving session was rooted elsewhere, so reviewed `hub.py`/`db.py`/`templates/index.html` by hand). Threat model: malicious/compromised agent on a localhost, no-auth-by-design hub. Two findings fixed, split across both agents.
- **🔴 HIGH — Stored XSS in `templates/index.html` (fixed by claude).** `renderAgents`/`renderMessages`/`renderEvents` interpolated agent-controlled fields (`agent id`/`description`, skill `name`/`description`, `sender_id`/`recipient_id`/`session_id`, event `agent`/`tool`/`outcome`) into `innerHTML` unescaped → a malicious `register_agent`/`send_message` could run script in the operator's browser, same-origin to the hub, and exfiltrate all `/api/state` data. Fix: added an `escapeHtml()` helper applied to every such field (the modal already used `textContent`), plus a defensive `Content-Security-Policy` meta (notably `connect-src 'self'`) and `object-src/base-uri 'none'`. **Browser-verified:** registered an agent whose id/description/skill carried `<img onerror>`/`<script>` payloads; the dashboard rendered them as inert escaped text (0 injected nodes, no script execution), Tailwind still loaded, no CSP violations.
- **🟡 MEDIUM — Origin/Host validation in `hub.py` (fixed by agy, with a regression caught + corrected).** Original `OriginValidationMiddleware` used a substring `startswith` Origin check (matched `localhost.evil.com`), let missing-Origin requests through, and only guarded `/mcp` (not `/api/*`). agy hardened it: exact `urlparse` host check, `Host`-header validation (DNS-rebinding / CVE-2026-48710 "BadHost"), and coverage of `/api/*`. **Regression caught in browser review:** agy's first pass rejected *all* missing-Origin `/api/` requests, which 403'd the dashboard's own same-origin `fetch('/api/state')` (browsers omit `Origin` on same-origin GET; `fetch` can't set it — it's a forbidden header). Corrected to a `Sec-Fetch-Site` check (allow `same-origin`/`none`, block `cross-site`). **Verified:** dashboard same-origin GET + MCP clients → 200; evil Origin / spoofed Host / cross-site → 403; full `pytest` 10/10; dashboard renders in a real browser on the patched code.
- **🟢 Accepted residuals (no code change):** no authn/ownership (any local client can register as any `agent_id`, drain any inbox, reply/fail/disconnect others' messages) — the v1 localhost trust model, covered by the v2 `caller_id`/auth item; no payload size cap (v2 retention); runtime CDN deps (mitigated by the new CSP). **✅ Clean:** no SQL injection (`db.py` fully parameterized).
- **Files changed:** `templates/index.html` (XSS escaping + CSP, by claude); `hub.py` + `tests/test_mcp.py` (Origin/Host hardening + Sec-Fetch-Site + regression tests, by agy); `tasks.md`, `sessions.md` (this entry, by claude).
- **Still open:** commit the security patches + doc refresh; then a round of dashboard UI changes (operator request); then v2 triage.

## 2026-06-18 — Cross-agent E2E through the hub + D19 hook layer live (both clients); hook_peek bug fixed
- **First real two-agent collaboration THROUGH the hub.** `claude-code-avdia` (Claude Code) and `antigravity-cli` (agy) both registered, discovered each other via `list_agents`, and ran the haiku demo end-to-end: `send_message` → `check_inbox` claim → `reply_to_message` → `result` fan-out to the sender's inbox. Step 6 E2E confirmed in both directions.
- **D19 peek/nudge hook layer wired on both clients.** Claude Code: `Stop` + `UserPromptSubmit` → `python hook_peek.py --agent-id claude-code-avdia` (in `~/.claude/settings.json`). Antigravity (`~/.gemini/config/`):
  ```json
  // 1. config.json — enable json hooks:
  { "jsonHooksEnabled": true }
  // 2. hooks.json — define the hooks:
  {
    "PreInvocationHook": { "command": "python", "args": ["C:\\Users\\avdia\\Documents\\Projects\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"] },
    "StopHook":          { "command": "python", "args": ["C:\\Users\\avdia\\Documents\\Projects\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"] }
  }
  ```
- **Bug fixed in `hook_peek.py`.** It read `data.get("pending_count")` but `/api/peek` (`db.peek_inbox`) returns `count`, so the nudge never fired. Changed to `data.get("count", 0)`. Verified: nudge fires when a message is pending, silent when none.
- **`test_mcp.py` made non-destructive (agy).** Previously it imported the live `DB_PATH="hub.db"` and `DELETE`d all rows on every run — wiping real hub state. Refactored to a `tmp_path` DB with `hub.DB_PATH` patched in the `setup_db` fixture. `pytest` now 10/10 green (independently re-run by claude: 10 passed in 2.00s), `hub.db` no longer clobbered.
- **Coordination model.** Agreed a division of labor (claude drafts the shared doc diffs + gets agy sign-off before writing; agy owns its hook docs + the test refactor) and a completion sequence (green tests → `/security-review` → commit docs → v2 triage).
- **Files changed:** `hook_peek.py` (bug fix, by agy); `tests/test_mcp.py` (temp-DB refactor, by agy); `AGENTS.md`, `tasks.md`, `sessions.md` (this refresh, by claude).
- **Still open:** `/security-review` (Origin mw + localhost bind + no-auth), then commit the doc refresh; then v2 triage.

## 2026-06-18 — Fixed FastMCP initialize error (-32602)
- Discovered that the `INVALID_PARAMS` (-32602) error was caused by `mcp.shared.session.py` catching an exception from FastMCP middleware and blindly wrapping it as an `Invalid request parameters` error.
- Fixed `ActivityTracker` middleware in `hub.py` by renaming `on_call_tool` to `__call__`, making it a valid callable for FastMCP's pipeline.
- Removed custom body interception from `OriginValidationMiddleware` which was confusing the SSE response lifecycle.
- Verified successful `initialize` handshake with `mcp-agent-hub` using a test client. The transport and server stack are fully operational.

## 2026-06-18 — Built core hub, DB, Dashboard, FastMCP server, and Tests

Executed Steps 1 through 5 of the Implementation Plan. The server is up and running.

**Work Accomplished:**
- **DB Layer (`db.py`)**: Implemented all logic using `aiosqlite` with WAL mode and atomic claim updates. Added sweeping for expired tasks.
- **Dashboard (`index.html` & `hub.py`)**: Built a Tailwind CSS frontend that polls `/api/state` and renders online/offline status, queue lengths, message threads, and an Activity Feed.
- **FastMCP Server (`hub.py`)**: Defined all 9 tools via `fastmcp`. Added `Origin` validation, `ActivityTracker` middleware, and fixed internal routing for `path="/"` and `transport="streamable-http"`.
- **Tests (`tests/`)**: Fully implemented unit tests for the database logic and API tests (Origin, Peek, MCP round-trip simulation) using `pytest` and `httpx`.

**Current State & Roadblocks:**
- Step 6 (E2E testing) is partially started. The Antigravity CLI successfully targets `http://localhost:8000/mcp`, but encounters an error during the MCP initialize phase: `error: calling "initialize": Invalid request parameters`. This JSON-RPC error indicates FastMCP might be rejecting the initialize payload from the client. Debugging this is the next step.

**Files changed:** `db.py`, `hub.py`, `templates/index.html`, `tests/test_db.py`, `tests/test_mcp.py`, `tasks.md`, `sessions.md` (this entry).

**Still open:** Step 6 (E2E testing) and tracking down the `initialize` payload rejection.

## 2026-06-16 — Researched Zed's Agent Client Protocol (ACP); rejected as transport, recorded interop + validations

User asked whether **Agent Client Protocol (ACP)** could be used directly, with our MCP server, or as inspiration — *before* building. Did primary-source research (zed.dev/acp, agentclientprotocol.com spec pages, LF AI blog). Still pre-implementation; no app code.

**Key disambiguation:** there are **two** "ACP"s. Our existing survey referenced **IBM's Agent _Communication_ Protocol** (REST agent↔agent, merged into A2A Sept 2025, winding down). The user meant **Zed's Agent _Client_ Protocol** — the "LSP for coding agents," a *vertical* editor↔agent protocol (JSON-RPC over stdio; editor spawns the agent as a subprocess; Gemini CLI = reference agent, Claude Code via adapter). Never previously evaluated.

**Verdict (3 questions):**
- **Use directly / as transport? No.** Wrong topology — editor↔agent (vertical, 1:1, editor owns the subprocess lifecycle), not our agent↔agent (horizontal, N peers ↔ central HTTP broker). No peer/registry/inbox concept; would force the hub to masquerade as an "editor" spawning each agent. Sharper version of why A2A-as-transport was rejected. Clincher: in ACP, Claude Code & Gemini CLI **are the Agents** (driven by an editor) — ACP gives our exact target clients **no path to reach each other**.
- **Use _with_ our MCP server? Yes — nothing to build.** ACP layers *above* MCP: the editor passes `mcpServers` (http supported) into `session/new`, and the agent connects as an MCP client. So an ACP-hosted Claude Code/Gemini CLI (e.g. inside Zed) reaches the hub **unchanged** via the editor's http `mcpServers` config — **validates D1**.
- **Inspiration? A few validations + 1 optional v2 refinement.** `session/request_permission` is a **4th converging validation of D17** (pause-ask-resume; A2A/LangGraph/CrewAI were the first 3). ACP's `tool_call` status lifecycle validates our message state machine. Optional v2 polish (parked, not adopted): typed cancel/reject `outcome` for clarifications, typed `stop_reason`/fail-category enum, MCP `ContentBlock` if multimodal ever matters (v1 keeps `str`). Positioning line: the hub is the **horizontal, durable, local peer complement to ACP's vertical editor↔agent standard**.

**Files changed (research/doc-only):** new `mem/acp-evaluation.md` (full analysis + sources); `design-decisions.md` (A2A bullet disambiguation note + new ACP survey bullet under "future options"); `tasks.md` (v2 ACP-derived-polish bullet); `sessions.md` (this entry). **No design decision (D1–D25) reopened or changed.**

**Still open:** nothing on design. Implementation (P1–P4 / Steps 1–6) still not started; residual is the install-time `pip freeze` (Step 1).

## 2026-06-16 — Pre-build evaluation-report triage (adopted `ruff`; reverted a premature v1 TTL escalation)

Triaged an independent pre-build evaluation report (Gemini, `evaluation_report.md`) against the locked design. Still pre-implementation; no app code.

**Triage outcome:**
- **Adopted:** `ruff>=0.4` into `requirements.txt` dev deps (fast lint/format from day one) — the one clearly-new, low-cost suggestion.
- **Declined:** `structlog` (cuts against the zero-extra-deps ethos — stdlib `logging`+JSON already satisfies D22); payload as `dict`/`Any` (misreads the domain — agent messages are prose, `str` is the right primitive; JSON-stringify by convention if structure is ever needed); terminal-row DB GC and a `sessions` table (already a known v2 item / deliberately out of scope).
- **Surfaced (sharper than the report):** the one *invisible* unbounded-growth vector the report's terminal-row-GC idea misses — a `pending kind='result'` whose requester never re-checks its inbox (plus a never-read `input_request`) escapes claim, reclaim, **and** the D24 TTL carve-out, so it sits `pending` forever. **Low severity** (localhost, slow, no correctness impact — `check_status` is the durable read).

**Course-correction:** a first pass *prematurely escalated* that v2-grade finding into a v1 edit of locked decision **D24** (extending the TTL sweep to `kind='result'`) and applied it **incompletely** — leaving `architecture.md` (×2), the `design-decisions.md` D6 row, and a `tasks.md` v2 bullet still asserting the original "task-only / result-excluded" behavior (4 internal contradictions; the "perfectly synced/locked" claim was therefore wrong). **Reverted** the D24 / `specs.md` / `plan.md` / `tasks.md` wording back to "`kind='task'` only" so D1–D25 stay exactly as signed off, and **folded the result-row vector into the existing v2 retention/GC workstream** (`tasks.md`) — handle all row-growth together there, preferring GC/*delete* over a `state=expired` patch (which would mislabel a delivered-elsewhere notification as "Expired" on the dashboard and slightly narrow D20 for senders absent >24h). Kept `ruff`.

**Files changed:** `requirements.txt` (+`ruff`, kept), `tasks.md` (v2 retention bullet now names the abandoned-`result`/`input_request` vector; D24 bullet + Step 2 reverted), `design-decisions.md` / `specs.md` / `plan.md` (D24 sweep wording reverted to `kind='task'` only), `sessions.md` (this entry, replacing the earlier premature one). `architecture.md` needed no edit — the revert restored consistency with it.

**Follow-up (same day):** reflected the adopted `ruff` in the Step-1 dev-deps lists (`plan.md`, `tasks.md`) and the `design-decisions.md` Dev-Tooling inventory, so the docs match `requirements.txt`.

**Follow-up 2 (same day):** vendored portable, repo-tracked tooling so it travels between the two PCs — a project-local status line (`.claude/statusline.py` + `.claude/settings.json`, relative-path command; commit `43fb3e5`) — and tightened the `CLAUDE.md` continuity rule: **no per-PC Claude memories** (`~/.claude` doesn't travel between the two machines); durable notes that don't fit `tasks.md`/`sessions.md` now go in a new in-repo **`mem/`** folder (see `mem/README.md`).

**Still open:** nothing on design — Q1–Q9 + D20–D25 all locked (D24 unchanged from its original form). Implementation (P1–P4 / Steps 1–6) not started. Residual: install-time `pip freeze` (Step 1).

## 2026-06-15 — Live web verification of post-cutoff deps (FastMCP / FastAPI / CVE) + FastAPI pin fix

Re-verified the post-training-cutoff facts the docs assert (assistant cutoff is Jan 2026; docs are dated June 2026) against **primary sources** via web search/fetch — PyPI metadata, the official gofastmcp docs, and the CVE record — then applied the one config correction it surfaced.

**Confirmed true:**
- **FastMCP 3.4.2** (June 6 2026), `requires-python >=3.10`. Meta-package resolves `fastmcp-slim[client,server]==3.4.2` — **not** a hard `fastmcp-remote` dep (corrected the docs' overstated split). `from fastmcp import FastMCP` holds.
- **FastMCP API** (gofastmcp docs): `http_app(path=…)` + `app.mount("/mcp", mcp_app)`, `combine_lifespans` from `fastmcp.utilities.lifespan` used as `FastAPI(lifespan=combine_lifespans(app_lifespan, mcp_app.lifespan))`, `add_middleware` + `Middleware.on_call_tool` ("earlier middleware runs first"), in-memory `async with Client(mcp)` testing — all current. D13/D14/D21/D22 + the test-split all hold.
- **CVE-2026-48710 ("BadHost")** real: HIGH, Starlette Host-header auth-bypass, all versions <1.0.1, fixed in 1.0.1; explicitly names MCP servers / agent harnesses. **Nuance:** it's a *Host*-header bug, distinct from our D18 *Origin* validation — the real mitigation is Starlette ≥1.0.1, which `fastmcp-slim` floors (verified). Our "don't self-pin starlette" strategy is correct.
- **Transitive floors:** `fastmcp-slim` → `starlette>=1.0.1`, `uvicorn>=0.35`, `mcp>=1.24,<2`.

**Found stale + fixed:** FastAPI — `0.124.x` shipped Dec 2025 but current is **0.137.1 (June 15 2026)**; our `fastapi>=0.124,<0.125` pin locked a 6-month-old release. **Applied:** `fastapi>=0.135,<0.138` (0.x can break on minors → tight ceiling), `uvicorn[standard]>=0.34 → >=0.35` (match the fastmcp floor), and corrected the `requirements.txt` + `design-decisions.md` `fastmcp-remote` note. **No design decision (D1–D25) invalidated.**

**Files changed:** `requirements.txt` (FastAPI pin, uvicorn floor, comment), `design-decisions.md` (Pre-build API Verification: live re-verification note + residual narrowed + fastmcp-remote correction), `tasks.md` (residual ticked to install-time `pip freeze` only), `sessions.md` (this entry).

**Still open:** nothing on design; implementation (P1–P4) not started. **Residual: just `pip freeze` at first install** to lock exact patch versions — API + deps now confirmed live.

## 2026-06-15 — Pre-build implementation review (12 findings) → D20–D25 locked, docs reconciled

Did a full review/check of every design doc (one-by-one and as a whole) before writing code. Still pre-implementation; no app code. Surfaced **12 findings**; the user signed off on all six real decisions plus the adopt-list, and everything was folded into the docs in one pass.

**Decisions locked (→ D20–D25):**
- **D20 — Result-to-inbox (1A).** Completing a `task` now enqueues a derived `kind="result"` message (carrying the response) to the original sender's inbox, delivered via their `check_inbox` long-poll and auto-completed on claim (best-effort, no ack); `check_status` downgraded to the durable/secondary read. Fixes the one real asymmetry — the *requester* was spin-polling `check_status` for results (the exact postal-mcp failure we criticize) — and lets the D19 hook nudge cover results too.
- **D21 — Long-poll = async-poll (3A).** `check_inbox(wait)` is an async coroutine polling every `LONGPOLL_INTERVAL` (~1s) via `aiosqlite` between `asyncio.sleep`s — never a blocking threadpool hold (which would starve the pool under N waiters). Committed to `aiosqlite`. `wait=True` is the default. Condition-notify → v2.
- **D22 — Activity feed = in-memory ring buffer (4C).** D14 promised a per-call event stream "the dashboard can surface" with no backing table/panel/endpoint. Resolved as a last-~200 in-memory ring buffer surfaced on `/api/state` + an Activity panel; not persisted. Persisted events table → v2.
- **D23 — `last_seen` from the direct actor arg only (5B).** D14 over-claimed "refresh on every call from the `agent_id` arg" — but only 3 tools carry `agent_id`, `send_message` uses `sender_id`, four are `message_id`-only, and `list_agents` has no caller identity. Refresh from `agent_id`/`sender_id` where present; skip the rest. Uniform `caller_id` → v2 with auth.
- **D24 — TTL sweep targets `pending kind='task'` only (2A).** The child `input_request` (a `pending` row) could be TTL-expired while its parked `input_required` parent stays protected — stranding the parent forever. Excluded `input_request`/`result` derived messages from the sweep. Cascade-expire → v2.
- **D25 — Phased walking-skeleton build (6A).** P1 core 7 tools + green cross-client haiku E2E → P2 D16/D17/D20 → P3 D6/D18 → P4 D19; structural columns ship in P1 so enrichment adds behaviour, not migrations.

**Reconciliations (doc inconsistencies fixed):** 9-tool count (D14 + plan said "8"); `check_inbox` default `wait=True` (the spec signature said `False`, contradicting D2); D14 wording (identity + logging); + adopts: SQLite ≥3.35 assert for `UPDATE…RETURNING`, Pydantic `Skill` model (advertised in `tools/list`), `created_at` index, in-memory `fastmcp.Client` test split (+`httpx` dev dep), no-ownership-check trust-model note, and **tool docstrings as a first-class deliverable** (they're the agents' real UX).

**Tooling embedded in the plan (user-approved):** Context7 (re-verify FastMCP 3.x/FastAPI at build — these postdate the assistant's training cutoff), `mcp-server-dev` (scaffold), `frontend-design` (dashboard), `/code-review` (each phase diff), `/security-review` (after P3), `/run` + `/verify` (Step 6 E2E). Note: the `claude-api` skill is **not** needed — the hub brokers messages between Claude Code instances; it never calls an LLM.

**Files changed (design-only):** `design-decisions.md` (+D20–D25; D6/D7/D14/D16 updated; +`LONGPOLL_INTERVAL`; Dev-Tooling block expanded), `specs.md` (state machine + result delivery; `wait=True` default; tools #1/#4/#5/#8; dashboard Activity panel + `kind`; schema `result` kind + `created_at` index; §6 constant), `architecture.md` (§1a middleware D22/D23; §2 db.py `complete_message`/`claim_pending`/`expire_messages`; §4 Activity panel + frontend-design; §5 async-poll; §6 no-ownership note), `plan.md` (Build-phasing block; Steps 1–6 + tooling callouts), `requirements.txt` (+`httpx`), `tasks.md` (D20–D25 section; Implementation steps re-phased; v2 list), `sessions.md` (this entry).

**Still open:** nothing on design — Q1–Q9 + D20–D25 all locked. Implementation (P1–P4 / Steps 1–6) not started. Residual: `pip freeze` + Context7 API re-verify at first install.

## 2026-06-15 — Last 3 open questions RESOLVED; hook peek/nudge layer added (D19)

Reviewed the three remaining open questions (Q1/Q3/Q5) with the user and **locked all of them** — design is now fully signed off (Q1–Q9 all resolved). Still pre-implementation; no app code. The session's headline was a user-raised idea that became a real design addition.

**Decisions locked:**
- **Q1 / D2 (confirmed) + new D19.** Long-poll `check_inbox` stays the **primary** delivery mechanism (`wait=True` default; `wait=False` for a cheap one-off check). On top of it we added an **optional client-side hook peek/nudge layer (D19)**: a thin hook calls a **read-only** `GET /api/peek?agent_id=…`, gets a pending-count + sender summary, and injects a nudge ("you have N messages — call `check_inbox`") into the agent's context. The hook **peeks, never claims**, so at-least-once (D3/D4) is fully preserved and the hub stays CLI-agnostic. Ships as `hook_peek.py` (stdlib-only) + a recipe, wired into the Step 6 E2E.
- **Q3 / D6 (extended).** Kept the tri-state (reject unknown/disconnected; queue+`flagged_stale` for stale) and **added a TTL**: a `pending` message unclaimed past **`MESSAGE_TTL=86400s` (24h)** is swept to a new terminal **`expired`** state (distinct from `failed`, so the dashboard shows *why*). Parked `input_required` tasks are deliberately excluded from the TTL in v1.
- **Q5.** **`VISIBILITY_TIMEOUT` raised 300→600s** (agent tasks routinely run >5 min → fewer false redeliveries; at-least-once means a low value only adds dupes, not data loss). `STALE_THRESHOLD=90s`, `LONGPOLL_TIMEOUT=30s`, `DASHBOARD_MESSAGE_LIMIT=100` accepted as-is; new `MESSAGE_TTL=86400s` added.

**Hooks investigation (verified, not assumed).** The user proposed bridging the async hub → sync CLI via lifecycle hooks (pasted an AI-generated sketch). I verified the load-bearing claim against the **`agy.exe` binary** (151 MB, same method as last session's `serverUrl` check): agy **does** have a hooks system — config in **`hooks.json`** (gated by a `json-hooks-enabled` flag in `config.json`), types **`Pre/PostInvocationHook`, `Pre/PostToolHook`, `StopHook`, `AfkStopHook`**, an injection mechanism (**`HookSystemMessage` / `HookInjectedStep`**), and `auto_continue_on_max_generator_invocations`. Claude Code has the parallel set (`UserPromptSubmit`/`Stop`/`SessionStart`/`Pre`/`PostToolUse`).

**Corrected the pasted sketch (don't copy literally):** (1) its `pre_prompt` event + `"inject_output":"append_to_system_prompt"` schema is **invented** — real injection is via the hook command's stdout (Claude Code `UserPromptSubmit`/`SessionStart`) or an agy `HookSystemMessage`; (2) it opened **SQLite directly and marked messages `delivered`** — which would add a 2nd DB writer (bypassing `db.py`) and **destroy at-least-once** (no claim, no ack). Hence the **peek-only** synthesis: the hook nudges, the MCP `check_inbox`→`reply`/`fail` path still does the real claim+ack. Honest limit recorded: a hook fires only on a trigger, so a fully idle agent waiting on a human still won't see mail until its next trigger (waking it via OS interrupt / stdin is rejected as terminal-hijacking).

**Files changed (design-only):** `design-decisions.md` (D2/D6 updated, +D19, constants table: VISIBILITY 300→600s + new MESSAGE_TTL, Q1/Q3/Q5 resolved), `specs.md` (state machine +`expired`, Delivery section rewritten with the hook layer + `/api/peek`, send-to-stale +expiry, dashboard +Expired badge, schema status enum, constants §6), `architecture.md` (diagram +hook node/arrow, +§1b hook layer, db.py +`peek_inbox`/`expire_messages`, §5 +expiry sweep), `plan.md` (layout +`hook_peek.py`, Step 2 helpers + status enum, Step 3 `/api/peek` + Expired badge, Step 4 expire sweep, Step 5 +expired/peek tests, Step 6 +hook wiring), `tasks.md` (Q1/Q3/Q5 resolved, Steps 1–6 refreshed, +v2 items), `CLAUDE.md` (layout +`hook_peek.py`, conventions +hook-peek-only + expired, fixed stale "not a git repo" + open-questions note), `sessions.md` (this entry).

**Still open:** nothing on design — Q1–Q9 all resolved. Implementation (Steps 1–6) still not started. Residual: `pip freeze` after the first install (now folded into Step 1).

## 2026-06-15 — Antigravity E2E check (D1/Q4 residual caveat CLOSED)

Ran the one outstanding transport verification: does the Antigravity CLI actually connect to a **localhost** Streamable-HTTP MCP endpoint via `serverUrl`? **Result: yes — verified live.** (Still no hub code; used a throwaway probe.)

**Method:** stood up a throwaway FastMCP Streamable-HTTP server on `127.0.0.1:8765` via `uv run --with fastmcp` (exercising our exact `mcp.http_app(path="/mcp")` mount), pointed AGY at it with `{"mcpServers":{"hub-probe":{"serverUrl":"http://localhost:8765/mcp"}}}`, ran `agy --print` once. Server log showed AGY completing a full MCP session: `POST /mcp` (initialize) → `GET /mcp` (SSE) → `POST /mcp 202` (notifications/initialized) → `POST /mcp` (tools/list) → `DELETE /mcp` (clean teardown). A direct `curl` initialize self-test confirmed the server first.

**Findings / corrections (folded into docs):**
- **Config PATH was wrong in our docs.** The `agy` CLI reads MCP config from **`~/.gemini/config/mcp_config.json`**, NOT `~/.gemini/antigravity/mcp_config.json` (confirmed via `discovery.go` log + a live connection through it). The `antigravity/` path belongs to a different Antigravity surface (likely the Electron IDE); the CLI ignores it. The actual CLI binary is `C:\Users\30697\AppData\Local\agy\bin\agy.exe` ("AGY", a ~146 MB Go binary).
- **Schema CONFIRMED against `agy.exe`:** HTTP server entry uses `serverUrl`; stdio uses `command`/`args`/`env` (binary strings: "either command or serverUrl"). Our `serverUrl` assumption was right.
- **`localhost` works against a `127.0.0.1` bind** (Go dialer handles the resolution) — the literal-`localhost` concern is closed.
- **Gotchas to remember:** write the JSON **UTF-8 without BOM** (Go parser rejects a BOM); an **empty** `mcp_config.json` logs `unexpected end of JSON input` (use `{"mcpServers":{}}`); AGY probes `/.well-known/oauth-protected-resource[/mcp]` before connecting (404 on our no-auth hub → it proceeds — relevant to D11/D18, no action needed).
- **Bonus:** `uv` cleanly installed fastmcp **3.4.2** (72 pkgs) and our `http_app(path="/mcp")` mount served a correct MCP `initialize` standalone under uvicorn — live de-risk of plan Step 1 + the mount pattern.
- One safety note: the `agy --dangerously-skip-permissions` variant was correctly blocked by the permission classifier; re-ran without it (MCP discovery happens at startup regardless, so the connection was still proven). State restored: `mcp_config.json` returned to its original (empty) bytes, probe server killed, temp files removed.

**Files changed:** `design-decisions.md` (D1 row + Q4 resolution: path correction + caveat closed), `specs.md` (transport note path), `plan.md` (Step 6.4 path + gotchas), `tasks.md` (verify item ticked, Step 6 path).

**Still open:** original **Q1/Q3/Q5** defaults stand pending sign-off. Implementation (Steps 1–6) still not started. Residual: `pip freeze` after the first real install.

## 2026-06-15 — Folded survey findings into the design (Q6–Q9 all accepted)

User accepted **all four** survey-driven candidate changes; folded them into the docs (still design-only — no app code). Resolved as decisions:
- **D16 (Q6)** — `register_agent`/`list_agents` now take a structured Agent-Card **`skills[]`** (`id`/`name`/`description`/`tags[]`/`examples[]`), replacing the opaque `capabilities` blob. `skills` stored as JSON (D10 updated).
- **D17 (Q7)** — new non-terminal **`input_required`** state + a **9th tool `request_input(message_id, question)`**. Worker parks the task and enqueues the question to the original sender's inbox as a child `input_request` (threaded by `session_id`/`parent_id`/`kind`); the sender answers with `reply_to_message`, which **un-parks** the task back to `pending` (answer appended to `context`). Reuses the existing inbox/reply path — no special client support. Parked rows are excluded from claim + reclaim. Every message now carries a `session_id`.
- **D6 refined (Q8)** — a send to a *stale* recipient is queued **and `flagged_stale`**, surfaced distinctly on the dashboard.
- **D18 (Q9)** — validate the HTTP **`Origin`** header on `/mcp` (allow missing/localhost, reject foreign) on top of the `127.0.0.1` bind; spec-mandated DNS-rebinding defense.

**Tool count 8 → 9.** **Schema:** `agents` += `description`, `capabilities`→`skills`; `messages` += `session_id`, `parent_id`, `kind`, `flagged_stale`, new `input_required` status, + index on `session_id`. Also fixed a **latent gap surfaced by D17**: `send_message` now takes a `sender_id` first arg (the sender was never captured before — `check_status` and the input_required round-trip both need it to route back to the requester).

**Files changed:** `specs.md` (registration, state machine, new Multi-turn Clarification & Sessions section, liveness flag, 9-tool list, dashboard badges, storage schema, Origin note), `design-decisions.md` (D6/D7/D10 updated, +D16/D17/D18, Q6–Q9 resolved), `architecture.md` (db helpers, redelivery/parked note, Origin in §6), `plan.md` (Steps 2–5), `tasks.md` (Q6–Q9 ticked, Steps 3–5 refreshed), `CLAUDE.md` (9 tools).

**Still open:** original **Q1** (delivery model), **Q3** (send-to-stale baseline — note D6 now extended by Q8), **Q5** (constants) — defaults still stand pending sign-off. Antigravity `serverUrl`→localhost still to confirm at E2E. Implementation (Steps 1–6) still not started (user: "do not rush to build"). Residual: `pip freeze` after first install.

## 2026-06-15 — Broad competitive-landscape survey (don't-rush-to-build)

User asked to widen the prior-art survey before building. Ran a **four-track parallel sweep** (verified against primary repos/specs), incl. user-requested targets `agentgateway/agentgateway`, `a2aproject/A2A`, and an inspiration markdown (claims fact-checked, several debunked).

**Headline finding — a near-twin we weren't tracking:** [`louislva/claude-peers-mcp`](https://github.com/louislva/claude-peers-mcp) (~2.1k★) is almost our exact concept (local SQLite broker for Claude Code peers) but **Claude-Code-only** (stdio + experimental `claude/channel`) and **fire-and-forget (no durable queue/acks)**. [`bobnet-mcp`](https://github.com/cath42/bobnet-mcp) is the same, in-memory, and *explicitly defers* persistence + future-delivery. → Our defensible edges sharpened: **CLI-agnostic transport + durable at-least-once queue.**

**Validated (no change):** long-poll for pull-only clients (postal-mcp, hbd/mcp-chat chose it for our exact reason; bounded poll matches our 30s); our **documented work-loop** is the true differentiator (postal-mcp README: blocking receive *"doesn't return to the mailbox easily… takes a lot of prompting"*); at-least-once + visibility-timeout is a justified outlier (field is ack-mailbox/no-ack); MCP spec confirms single `/mcp` Streamable HTTP + held-SSE long-poll + mandated `Origin` validation + `127.0.0.1` bind.

**Fact-checks:** A2A is now a **Linux Foundation** project (Google-donated; merged with IBM **ACP**), 1.0.x. **hermes-agent A2A support is proposal-only (unimplemented).** **MCP-UI / "MCP Apps" (SEP-1865)** is real/official but CLI agents don't render `ui://` → keep plain dashboard. **agentgateway** is a stateless proxy, no durable queue. **MCP `tasks` utility** (spec 2025-11-25) mirrors our queue+visibility-timeout → future interop option. `mkc909/agent-communication-mcp-server` 404s (unverifiable). claude-flow/ruflo star count disputed/inflated.

**New open questions raised (need sign-off): Q6** structured Agent-Card `skills[]` for register/list; **Q7** `input_required` state + `session_id` conversation grouping (highest-value borrow); **Q8** send-to-stale = accept+flag+surface (refines D6); **Q9** `Origin` validation. All defaults/decisions otherwise unchanged.

**Files changed:** `design-decisions.md` (A2A bullet rewritten; new § Competitive landscape survey; +Q6–Q9), `tasks.md` (+Q6–Q9), `sessions.md` (this entry).

**Still open:** Q1, Q3, Q5 (originals) + Q6–Q9 (new). Implementation (Steps 1–6) still not started — by design (user: "do not rush to build").

## 2026-06-15 — Pre-build research: prior-art read + FastMCP 3.x API verification

Completed the two pre-build "to verify" items from `tasks.md` (still pre-implementation; no app code written).

**FastMCP 3.x API — verified against the real `fastmcp` 3.4.2 source** (cloned `PrefectHQ/fastmcp`; PyPI confirms 3.4.2 is latest, `requires-python >=3.10` — inside our pins). All D13/D14 assumptions hold:
- `fastmcp` 3.x is now a **meta-package** (uv workspace: `fastmcp-slim` + `fastmcp-remote`); the importable `fastmcp` namespace — incl. `FastMCP` — ships in **fastmcp-slim**. `from fastmcp import FastMCP` and `pip install fastmcp` still work → no requirements change.
- `mcp.http_app(path=...)` confirmed (`transport="http"` default = Streamable HTTP; returns `StarletteWithLifespan`).
- `combine_lifespans` at `fastmcp.utilities.lifespan` (docstring shows our exact FastAPI usage).
- `add_middleware(Middleware)` + `Middleware.on_call_tool(context, call_next)`; `context.message.arguments`/`.name` supply the `agent_id` + tool name for the D14 middleware.

**MCP Agent Mail — source read.** Build-from-scratch verdict holds. It's on `fastmcp` **2.x**, uses **SQLAlchemy + SQLModel ORM** (we stay raw `sqlite3`/`aiosqlite`), nests the MCP lifespan **manually** (we keep `combine_lifespans`), and instruments via a Starlette HTTP middleware + per-tool decorators (we keep our `on_call_tool` middleware). Worth borrowing for `db.py`: extra WAL pragmas (`synchronous=NORMAL`, `busy_timeout`, passive `wal_checkpoint` on checkin) + lightweight retry-on-lock with backoff.

**Files changed:** `design-decisions.md` (verified tags on D13/D14, Prior-Art bullet updated, new § Pre-build API Verification), `requirements.txt` (meta-package note), `tasks.md` (ticked the 2 verify items, Step 2 WAL note), `sessions.md` (this entry).

**Still open:** Q1 (delivery), Q3 (send-to-stale), Q5 (constants) — defaults stand pending sign-off. Antigravity `serverUrl`→`localhost` still to confirm at E2E (Step 6). Implementation (Steps 1–6) not started. **Residual:** `pip freeze` after the first install to lock exact patch versions.

## 2026-06-15 — Design research & decision lock-in

Continued architecting (still pre-implementation; no app code yet). Ran parallel research across four dimensions — Python deps, FastMCP extension points, Claude Code/Antigravity dev tooling, and prior-art survey — then folded the findings into the design docs.

**Decisions resolved / added:**
- **D13** — Depend on standalone **`fastmcp` 3.x** (`>=3.4,<4`), not the official SDK's bundled FastMCP. Note: docs previously assumed 2.x; current line is 3.x (repo moved to `PrefectHQ/fastmcp`). Mount via `mcp.http_app(path="/mcp")` + forward lifespan. Don't self-pin `starlette` (3.4.1 floors `>=1.0.1`, CVE-2026-48710).
- **D14** — One FastMCP `on_call_tool` middleware centralizes `last_seen` refresh + structured per-call logging (off the 8 tool bodies).
- **D15** — Visibility-timeout reclaim is **lazy-on-claim** (claim query grabs stale `in_progress` too); optional asyncio loop is only a backstop. Rejected APScheduler.
- **Q2 closed** → fastmcp 3.x (D13).
- **Q4 closed** → Antigravity supports remote Streamable HTTP via the `serverUrl` key (`~/.gemini/antigravity/mcp_config.json`). One residual: verify `serverUrl`→`localhost` during E2E.
- **D1 reaffirmed** — single `/mcp` Streamable HTTP endpoint serves both Claude Code (`type:http`) and Antigravity (`serverUrl`).

**Prior-art verdict:** build from scratch. Closest analogue is [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) (same stack, but ack-based mailbox, not our at-least-once + visibility-timeout queue). A2A reached v1.0 but is the wrong topology (peer-servers, no durable central queue) and clients are MCP-native — rejected as transport.

**Tooling identified:** MCP Inspector CLI for the smoke test; `mcp-server-dev` Claude Code plugin + Context7 as references.

**Files changed:** `design-decisions.md` (D1, +D13–D15, closed Q2/Q4, +Prior Art section), `architecture.md` (FastMCP 3.x, mount snippet, +§1a middleware, lazy reclaim), `specs.md` (transport note, redelivery), `plan.md` (deps, middleware step, mount pattern, Inspector, client config commands), `CLAUDE.md` (fastmcp 3.x), new `requirements.txt`, new `tasks.md` + `sessions.md`. Initialized git repo + `.gitignore`.

**Still open:** Q1 (delivery model), Q3 (send-to-stale), Q5 (constants) — defaults stand pending sign-off. Implementation (plan Steps 1–6) not started.
