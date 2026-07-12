# Pending Tasks — MCP Agent Hub

> **This file is the source of truth for what's left to do.** This project travels between two PCs and uses **no local Claude memories** — anything worth preserving lives here (pending work) or in `sessions.md` (history of what's done). Update both in the same change as the work.

> **▶ START HERE (next session).** **2026-07-12 (evening): PUBLISHED — AHB-10 closed, the
> last open issue.** The repo is public at **https://github.com/avdiam/mcp-agent-hub** (MIT)
> with a GitHub Pages docs site at **https://avdiam.github.io/mcp-agent-hub/** (static HTML
> served from `docs/`; remember: push to `master` auto-redeploys it). Pre-publish pass:
> 10 stale one-off scripts deleted; README rewritten as a landing page; three new user
> guides (`docs/setup.md`, `connect-an-agent.md`, `how-it-works.md`) + matching HTML;
> LICENSE added; the stale flat `hooks.json.template` fixed; secrets/history scan clean.
> URL broadcast to all peers on the hub. Earlier same day: `antigravity` (agy CLI) fully
> onboarded live and **AHB-18 fixed + deployed** (`register_agent` skills now optional,
> non-clobbering re-register) — confirmed working by the reporter. `pytest` **67/67**.
> **Next candidates:** dogfood the board with the now-4-strong peer roster, remaining
> workstream-2 UI ideas (send/requeue from the UI, search, richer metrics), stress-test
> round 2. —
> Prior: **2026-07-12 (later): dashboard SSE push shipped (D38) —
> workstream 2's big remaining item.** New `GET /api/events` SSE stream pushes the full
> `/api/state` snapshot on every state change (in-process `StateNotifier` bumped by the
> ActivityTracker on every tool call, by mutating REST endpoints + `/api/peek`, and by the
> sweeper only when a pass changed rows; 250 ms debounce, 20 s keepalives). Frontend defaults
> to a new **Live** mode (`EventSource`, auto-reconnect, auto-fallback to 2 s polling) and
> re-renders only panels whose data changed (per-panel fingerprints) — idle hub = zero
> re-renders, jitter gone. `OriginValidationMiddleware` rewritten as **pure ASGI** (closes the
> AHB-14 item-3 watch-item; SSE regression tests run over a real uvicorn through it — httpx's
> ASGITransport buffers whole bodies and can't consume SSE). `pytest` **66/66** (62 → +4);
> verified live end-to-end (real MCP calls pushing to a watching Chrome dashboard, hostile
> Origin/Host 403s, kill-server → EventSource reconnected on its own). Design confirmed with
> avdia pre-build (SSE over WebSocket; fold in the ASGI rewrite). **Next candidates:**
> AHB-10/publish (the last open issue), dogfood the board with more peers, remaining
> workstream-2 UI ideas (send/requeue from the UI, search, richer metrics). —
> Prior: **2026-07-12: first real job-board run with `wiki-forge`
> (green end to end) + D37 hardening (AHB-16/AHB-17 fixed).** wiki-forge autonomously read the
> advert, claimed offer `cc076b7b` (MCP-vs-A2A Q&A), got selected, worked the auto-created task
> via /wiki-ask, and fanned back a cited answer + a 4-point friction report — all mechanics
> behaved. The friction became same-day fixes (D37): **purge now deletes the sender's
> `broadcasts` audit rows** (AHB-16 — the ghost SMOKE advert was the first thing wiki-forge saw);
> **fulfilled offers flip to terminal `completed`** (success mirror of the failure re-open; live
> offer backfilled); **claim_offer returns the outcomes contract** (`expires_at` + "every outcome
> arrives in your inbox — no polling"; a claim-receipt push was declined as duplication); and the
> **payload authoring convention** documented (payload = pure work statement — the hub appends
> claim instructions to the advert). `pytest` **62/62**; deployed. **Next candidates:** dashboard
> SSE push (workstream 2), AHB-10/publish. —
> Prior: **2026-07-11 (later 6): AHB-2 shipped (D36) — the job-offer
> board.** A **poster-picks auction** on top of the existing machinery: `post_offer` (advert
> broadcast under the poster's own D33 caps, all-or-nothing) → `claim_offer` (claims accumulate,
> no window; poster notified via new ack-less `kind="offer_update"`) → `resolve_offer('select')`
> (winner assigned; payload auto-sent as a **normal task**, `session_id = offer_id`, so
> result/failure fan-backs just work; losers notified) or `'withdraw'`; failed assignment
> **re-opens** the offer within TTL; `expire_offers` sweeps; `list_offers` browses. Tables
> `job_offers`/`job_claims`; tools **10 → 14**; caps mirror D33 + 5 open offers/poster; purge
> delete covers the board; read-only dashboard **Job Board** panel. Design confirmed with avdia
> pre-build (auction > first-claim-locks; auto-create task > match-only; panel included).
> `pytest` **59/59**; live MCP smoke 10/10 with probe agents (purged after); deployed. **Next
> candidates:** dashboard SSE push (workstream 2), AHB-10/publish, dogfood the board with real
> peers (wiki-forge/nexus are registered but were stale today). —
> Prior: **2026-07-11 (later 5): AHB-1 P2 shipped (D35).** Durable
> announcements via **register-time catch-up**: `register_agent` queues any broadcast from the last
> 24h the registrant never received (structural `session_id = broadcast_id` dedupe — no new table,
> no read cursor; the P1 `broadcasts` audit table + a `context` column is the whole store), plus a
> dashboard **Broadcast** control (`POST /api/broadcast` as `operator`, same caps, compose modal).
> `pytest` **44/44**. **AHB-1 is now fully fixed (P1 D33 + P2 D35).** Earlier same day: live
> validation with `wiki-forge` 3/3 (task→result, broadcast, failure fan-out); **AHB-15 fixed via
> D34** (liveness-derived `list_agents` status, one shared source with `/api/state`); its
> `/wiki-serve` SKILL.md delta re-review delivered. **Next candidates:** AHB-2 (job-offer board —
> now unblocked, builds on broadcast), dashboard SSE push (workstream 2), AHB-10/publish. —
> Prior context: **2026-07-11 eval + fixes (committed).** A full project review (avdia-requested) found and **fixed AHB-11** — the D20 result / D17 `input_request` fan-out **crashed when the original sender was offline / unknown / deleted** (raised back to the worker even though the task completed; observed live in `hub.log`) → now bypassed via **D30** (`enqueue_message(internal=True)`, mirrors the AHB-1 **BD3** rule, so AHB-1 P1 inherits the fix) — and **AHB-12** — a duplicate/late `input_request` reply **revived an already-completed parent task** → now un-parks only when the parent is still `input_required`. **Then fixed AHB-13 via D31** — `fail_message` now (#3) fans a **`kind="failure"`** message to the sender's inbox (mirror of the D20 result fan-out; ack-less via the new `NO_ACK_KINDS`, internal so it survives a departed sender) and (#4) returns a **failed `input_request`'s** parent to `pending` with the refusal noted, instead of stranding it `input_required` forever — closing the D20 "failures were invisible" gap and the explicit-fail slice of the v2 cascade-expire item. Dashboard badges the new **FAILURE** kind. **Then fixed AHB-14 via D32** (hardening pass): DB tunables (`STALE_THRESHOLD`/`VISIBILITY_TIMEOUT`/`MESSAGE_TTL`) now **single-sourced in `db.py`** and imported by `hub.py` (no more hardcoded `90`/`600` desync), and the activity feed **attributes the message-id-only tools** (`reply`/`fail`/`request_input`/`check_status`) to the real acting agent instead of "System" (display-only; D23 `last_seen` untouched). Item 3 (BaseHTTPMiddleware-over-SSE) left as a documented watch-item. Regression tests added; `pytest` **26/26**. **Then shipped AHB-1 P1 via D33** (broadcast/announce): new 10th MCP tool **`broadcast_message`** → `db.broadcast` fans an ack-less **`kind="announcement"`** to every non-offline agent (sender included, BD5; offline skipped, BD3), flood-capped (cooldown + hourly + payload/subject size + recipient ceiling) via a durable **`broadcasts`** audit table; `announcement` joined `NO_ACK_KINDS` + the TTL sweep; dashboard badges **ANNOUNCE**. 9 new tests; `pytest` **35/35**. **AHB-11/12/13/14 fixed and AHB-1 P1 shipped — all committed.** See `agent-hub-issues.md` + the newest `sessions.md` entry. — Prior state: v1 post-v1 polish is **complete, committed, and verified.** Steps 1–6 are fully done, including README install fixes, test-agent cleanups, MCP Inspector CLI smoke checks, and the D28 decision-log entry. **Next actions:** **AHB-1 P1 is now BUILT & committed (D33)** — validate it in real use, then the next candidates are **AHB-1 P2** (durable announcements / MOTD so agents connecting *later* also receive a broadcast — tables + read-cursor + deliver-on-register/`get_announcements` + a dashboard "Broadcast" control) and **AHB-2** (job-offer board, which builds on the broadcast primitive). Also still open: the dashboard **SSE/WebSocket push** to replace the 2 s poll (workstream 2), and **AHB-10** (publish the repo so peers can self-serve re-vendors). **AHB-3/5/8/9 — ALL FIXED & VERIFIED BOTH SIDES (2026-06-20), committed `549120c`.** AHB-3 (D29): `/api/peek` now `touch_last_seen`s the queried agent (Option A) — no client re-vendor, unit-tested. AHB-8: `SessionStart` sentinel-clear recipe (crash-safety, `$CLAUDE_PROJECT_DIR`). AHB-9: canonical `hub_peek.py` nudge converged with `wiki-forge`'s fork (names ack tools; identity-constant override documented). `wiki-forge` re-vendored clean (`6c87505`), 8/8 verified; `nexus` pinged (re-vendor low-urgency/async, no contract change). **Dogfood win:** first live `/wiki-serve` autonomous round-trip succeeded (MCP-vs-A2A cited answer) — validates workstream 4. **Pending from peer `wiki-forge`:** a `/wiki-serve` SKILL.md draft incoming as a hub task for review (its autonomous wiki task-fulfillment skill). Otherwise triage v2 items (DB connection pooling, condition-notify long-poll, persisted events table, cascade-expire parked tasks). Skim the newest `sessions.md` entry first.

## Roadmap / Product Direction (set 2026-06-18)

The hub stays **single-user / many local agents** for the **short + mid term**. **Multi-user + networked** is an explicit **long-horizon** goal — *design new work to stay compatible* with a future `caller_id`/auth model (D11/D23 v2), but **don't build auth/networking yet**. Open-sourcing the code (Option D, see Distribution) is orthogonal to networking the runtime: we publish **as one-user/localhost first**, with the multi/network path on the horizon.

**Short/mid-term workstreams (before publishing):**
1. **Stress-test & stabilize** — ✅ **ROUND 1 DONE (2026-06-18).** Built two load harnesses (db-level `scripts/stress/db_stress.py` + antigravity-2's HTTP-level `:8100` harness); fixed the SQLite WAL write-contention bug (`busy_timeout`/`synchronous` missing on operational conns + retry-on-lock — commits `76cb3d5`/`060e77d`) and a `NoneType` crash on unknown `message_id` (`c27d993`). Result: lock errors **1,169 → 0**, success **1.7% → 100%**, throughput **16.5 → 76.1 MCP calls/s**, p95 **13.8s → 1.4s**; atomic-claim correctness held (0 double / 0 lost @ 2000×32); `pytest` 12/12. Connection pooling **deferred** (see v2). See `sessions.md`. **Round-2 candidates (not yet done):** visibility-timeout redelivery under simulated crash (D3), large/many-message payloads, dashboard `/api/state` perf under load.
2. **Web dashboard — more interactive & useful** — ⏳ **ITERATION 1 DONE & VALIDATED (2026-06-18), workstream still open.** Shipped: fixed the activity/staleness middleware bug (real caller+tool, agents flip online); operator **disconnect** + **purge** buttons (live endpoints); foldable agents; clickable **Live Activity → detail modal**; message **2–4 word titles** + **session/stream grouping** + **per-agent filters** + **friendly stream/session titles** (e.g. `Haiku about APIs (bde4ba65)`); stat tiles; poll-interval control; copy buttons; `/favicon.ico` dummy route; and CSP `frame-ancestors` HTTP response header. Commits: backend `c262e76`/`c80ba2f` (agy), frontend `5427177`/`09058c4`/`f433067` (claude/agy); antigravity-2 full browser E2E = all green. **(a) DONE 2026-07-12 (D38):** SSE push (`/api/events` + `StateNotifier`) replaced the 2 s poll, with per-panel fingerprint rendering on the client (both halves of the item — push *and* diff-based DOM updates); polling survives only as the fallback. **Still open:** (b) possible further UI: send/requeue/expire from the UI, search, richer metrics/thread view.
3. **New features** — pull forward high-value items that don't need networking (e.g. typed `stop_reason`/fail-category enum, clarification cancel/reject `outcome`, message search/labels — to be scoped). **Broadcast/announce (AHB-1) — FULLY SHIPPED 2026-07-11:** P1 (D33, flood-capped fan-out) + P2 (D35, register-time late-joiner catch-up + dashboard broadcast control). **Job-offer board (AHB-2) — SHIPPED 2026-07-11 (D36):** poster-picks auction, 4 new tools, dashboard Job Board panel; dogfood it with real peers next.

> **Maintainer & issue intake.** The `agent-hub-builder` agent is the point of contact for the hub (hooks, the `agent-hub-live` skill, usage, friction). Reported friction/bugs/feature requests are logged in [`agent-hub-issues.md`](agent-hub-issues.md) and worked off from there.
4. **Dogfood** — use the hub to coordinate our own work with additional **specialist agents** (beyond `claude-code-avdia` + `antigravity-cli`).

**Then:** publish to GitHub as **one-user/localhost** (Option D, source distribution), trust model clearly documented, with the multi-user/networked evolution stated as the roadmap.

## Design questions — ALL RESOLVED (as of 2026-06-15)
- [x] **Q1 / D2 — Delivery model.** RESOLVED (2026-06-15) → **D2 (confirmed) + D19**: long-poll `check_inbox` stays primary (`wait=True` default; `wait=False` for one-off checks); **added an optional hook peek/nudge layer** — read-only `/api/peek` + a shipped `hook_peek.py` (Claude Code `Stop`/`UserPromptSubmit`; agy `StopHook`/`PreInvocationHook`). The hook **peeks, never claims**, so at-least-once is preserved. agy's hook system verified from the `agy.exe` binary.
- [x] **Q3 / D6 — Send-to-stale policy.** RESOLVED (2026-06-15) → **D6 (extended)**: explicit `disconnect` blocks new sends; mere staleness still queues (+`flagged_stale`); **plus** a `pending` message unclaimed past `MESSAGE_TTL` (24h) is swept to a new terminal **`expired`** state (distinct from `failed`).
- [x] **Q5 — Tunable constants.** RESOLVED (2026-06-15): **`VISIBILITY_TIMEOUT` raised 300→600s**; `STALE_THRESHOLD=90s`, `LONGPOLL_TIMEOUT=30s`, `DASHBOARD_MESSAGE_LIMIT=100` accepted as-is; **new `MESSAGE_TTL=86400s`** added (D6/Q3 expiry sweep).
- [x] **Q6 — Structured capability descriptor.** RESOLVED (2026-06-15, user accepted) → **D16**: Agent-Card `skills[]` on `register_agent`/`list_agents`. Folded into specs/architecture/plan.
- [x] **Q7 — `input_required` state + `session_id` grouping.** RESOLVED (2026-06-15, user accepted) → **D17**: new `input_required` state, 9th tool `request_input`, `session_id`/`parent_id`/`kind` on messages, un-park-on-reply rule. Folded into docs.
- [x] **Q8 — Send-to-stale: flag + surface.** RESOLVED (2026-06-15, user accepted) → **D6 (refined)**: `flagged_stale` on stale sends, surfaced on the dashboard. Folded into docs.
- [x] **Q9 — `Origin`-header validation.** RESOLVED (2026-06-15, user accepted) → **D18**: validate `Origin` on `/mcp` alongside the `127.0.0.1` bind. Folded into docs.

## Implementation-review decisions — LOCKED (2026-06-15) → D20–D25

A pre-build *implementation* review (12 findings) surfaced six decisions, all signed off and folded into the docs:
- [x] **D20 — Result-to-inbox delivery.** Completing a `task` enqueues a `kind="result"` message (carrying the response) to the original sender's inbox, delivered via their `check_inbox` long-poll and auto-completed on claim (best-effort, no ack); `check_status` is the durable/secondary read. Removes requester-side spin-polling + lets the D19 hook nudge cover results.
- [x] **D21 — Long-poll is async-poll.** `check_inbox(wait)` = async coroutine polling every `LONGPOLL_INTERVAL` (~1s) via `aiosqlite` between `asyncio.sleep`s — never a blocking threadpool hold. `wait=True` is the default. (Condition-notify → v2.)
- [x] **D22 — Activity feed = in-memory ring buffer** (last ~200 events) surfaced on `/api/state`; not persisted. Supersedes D14's unbacked "dashboard can surface". (Persisted events table → v2.)
- [x] **D23 — `last_seen` from the direct actor arg only** (`agent_id`/`sender_id`); message-id-only tools + `list_agents` don't refresh. (Uniform `caller_id` → v2 with auth.)
- [x] **D24 — TTL sweep targets `pending kind='task'` only** — `input_request`/`result` excluded (no stranded parents). (Cascade-expire → v2.)
- [x] **D25 — Phased walking-skeleton build** (P1 core + green haiku E2E → P2 D16/D17/D20 → P3 D6/D18 → P4 D19). Structural columns ship in P1.

Reconciliations now consistent across docs: 9-tool count (was "8" in spots); `check_inbox` default `wait=True` (the spec signature said `False`); D14 wording; SQLite ≥3.35 assert; Pydantic `Skill` model; `created_at` index; in-memory `fastmcp.Client` test split (+ `httpx` dev dep); no-ownership-check trust-model note; tool docstrings as a first-class deliverable.

**Tooling locked into the plan:** Context7 (Step 1/4/5 API re-verify), `mcp-server-dev` (scaffold), `frontend-design` (Step 3 dashboard), `/code-review` (each phase diff), `/security-review` (after P3), `/run` + `/verify` (Step 6 E2E).

## To verify (not blockers, but confirm before locking)
- [x] **Antigravity → `localhost` over Streamable HTTP.** VERIFIED 2026-06-15 — the AGY CLI (`agy --print`) completed a full MCP handshake (initialize → SSE → tools/list → clean teardown) against a localhost Streamable-HTTP server via `serverUrl`. **Path correction:** AGY CLI reads `~/.gemini/config/mcp_config.json` (not `~/.gemini/antigravity/…`). Write UTF-8 no-BOM. D1 caveat closed. See `sessions.md`.
- [x] **Read [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) source** — done 2026-06-15. Build-from-scratch verdict holds. It's on `fastmcp` 2.x + SQLAlchemy/SQLModel ORM; nests the MCP lifespan manually; instruments via Starlette HTTP middleware + per-tool decorators. **Borrow for `db.py`:** extra WAL pragmas (`synchronous=NORMAL`, `busy_timeout`, passive `wal_checkpoint`) + lightweight retry-on-lock. See `design-decisions.md` Prior Art.
- [x] **Verified the FastMCP 3.x API** — twice: against the real `fastmcp` 3.4.2 source (2026-06-15) and **re-confirmed live via web** (PyPI + gofastmcp docs): `http_app(path=…)` / `combine_lifespans` (`fastmcp.utilities.lifespan`) / `add_middleware`+`on_call_tool` / in-memory `Client(mcp)`. Transitive floors confirmed: `fastmcp-slim` → `starlette>=1.0.1` (CVE-2026-48710 "BadHost" fix), `uvicorn>=0.35`, `mcp>=1.24,<2`. FastAPI pin was stale (`<0.125`; current 0.137.1) → bumped to `>=0.135,<0.138`. **Residual: only the install-time `pip freeze`** (Step 1) to lock exact patch versions — API + deps now confirmed.

## Implementation (per plan.md — not started; pre-implementation phase)

Build in **phases** (D25): **P1** skeleton + green haiku E2E → **P2** skills/`input_required`/result-to-inbox → **P3** expiry/`flagged_stale`/Origin → **P4** hook layer. Structural columns ship in P1.

- [x] **Step 1 (P1)** — venv + `pip install -r requirements.txt` (+ dev `pytest`/`httpx`/`ruff`) + **`pip freeze`** + create directory structure (incl. `hook_peek.py`). **Context7** re-verify the FastMCP 3.x API + FastAPI/uvicorn; optional `mcp-server-dev` scaffold.
- [x] **Step 2 (P1+)** — `db.py`: `init_db()` (WAL, indexes incl. `created_at`, **assert SQLite ≥3.35**), `aiosqlite` connection helper (D21), registry helpers, message helpers incl. atomic **lazy-on-claim** `claim_pending` (grabs `pending` + stale `in_progress`; excludes parked `input_required`; **auto-completes `kind='result'` on claim — D20**), `reclaim_stale` backstop, **`peek_inbox`** (read-only — D19), **`expire_messages`** (`pending kind='task'`→`expired` — D6/Q3/D24), and `complete_message` with the **result fan-out (D20)** + **un-park (D17)** rules. Status enum includes **`expired`**; `kind` includes **`result`**. *(Borrow WAL pragmas + retry-on-lock from MCP Agent Mail.)*
- [x] **Step 3 (P1, enrich P2+)** — dashboard via **`frontend-design`**: `index.html` (Tailwind CDN, status badges incl. **Input Required**/**Failed**/**Expired**, **`kind`** indicator incl. **result**, **⚠ stale-recipient** flag, per-agent **skills**, `session_id` grouping, payload/response/question modal, **Activity panel — D22**), `/` route, `/api/state` (agents + recent messages + **events** + stats), read-only **`/api/peek`** (D19).
- [x] **Step 4 (P1, enrich P2–P4)** — `hub.py`: `FastMCP` instance, cross-cutting `on_call_tool` middleware (**`last_seen` from direct actor arg — D23** + **in-memory activity ring buffer — D22**), the **9 `@mcp.tool`s** (docstrings as first-class deliverable; `check_inbox` **`wait=True` async-poll — D21**; `reply_to_message` **result fan-out — D20** + un-park; `register_agent` Pydantic **`Skill`** — D16), mount via `mcp.http_app(path="/", transport="streamable-http")` + `combine_lifespans`, **`Origin`-validation middleware** (D18), lazy reclaim + optional asyncio backstop running **`reclaim_stale` + `expire_messages`** (D15/D24), bind `127.0.0.1`.
- [x] **Step 5** — tests: DB unit tests (atomic claim under concurrency, visibility redelivery, **skills** JSON round-trip, offline-vs-stale, **`input_required` park/un-park**, **`flagged_stale`**, **`expired` sweep**, **`peek_inbox` non-mutating**, **result fan-out — D20**) via in-memory **`fastmcp.Client`** + one real-HTTP check (Inspector CLI) + `Origin`-rejection + **`/api/peek`** check. **`/code-review`** each phase's diff.
- [~] **Step 6** — E2E via **`/run`** + **`/verify`**: run server, open dashboard, wire Claude Code (`type:http`) + Antigravity (`serverUrl` in `~/.gemini/config/mcp_config.json` — verified initialize transport), run the haiku cross-agent demo, confirm the loop; then **wire the D19 hook layer** (`hook_peek.py` into Claude Code `Stop`/`UserPromptSubmit` + agy `StopHook`/`PreInvocationHook`) and confirm the peek-nudge path. **`/security-review`** after P3.
  - [x] 2026-06-18: haiku cross-agent E2E ran both directions through the hub.
  - [x] 2026-06-18: D19 hook layer wired + verified on **both** clients (Claude Code + agy).
  - [x] 2026-06-18: `hook_peek.py` `count`/`pending_count` bug fixed; nudge now fires.
  - [x] 2026-06-18: `test_mcp.py` made non-destructive (temp DB); `pytest` 10/10 green.
  - [x] 2026-06-18: security review done — HIGH dashboard XSS (escaping + CSP) + MEDIUM Origin/Host hardening (Sec-Fetch-Site) both fixed & browser-verified; `pytest` 10/10.
  - [x] 2026-06-18: security patches **committed** (`a9b6e66`); D18 hardening folded into the decision log.
  - [x] 2026-06-18: **recovery controls (D26)** — soft Reset (`/api/reset`) + hard Restart (`/api/restart`) + `run_hub.py` supervisor (exit-code-42 relaunch); `pytest` 12/12 (added `test_api_reset`/`test_api_recovery_middleware`); browser-verified on `:8001` (caught + fixed a restart-overlay race). Commits `ff331ca`/`acc9e61`.
  - [x] 2026-06-18: **repo restructure (D27)** — `mcp_hub/` package, `docs/dev/`, `scripts/`, root `README.md`; run via `python run_hub.py` (`uvicorn mcp_hub.hub:app`). Logs consolidated to `logs/hub.log`. Commits `1e7e8da`/`ae99028`. Re-verified live (imports, templates path, restart-from-root, dashboard renders, 403s hold).
  - [x] 2026-06-18: README install fixes (agy), test-agent/test_agent database cleanup, and Step 5.2 Inspector CLI smoke check verified. All v1 post-v1 polish closed. D28 decision log entry written.

## Distribution
- [x] **Publish as a public open-source GitHub repo (Option D: Open Source)** — ✅ **DONE
  2026-07-12**: **https://github.com/avdiam/mcp-agent-hub** (public, MIT, source
  distribution — no PyPI/Docker, as decided) + GitHub Pages docs site
  **https://avdiam.github.io/mcp-agent-hub/** (static HTML from `docs/` on `master` —
  every push redeploys it). URL broadcast on the hub so peers pin the real origin;
  re-vendoring is now `git fetch && checkout <hash>` (closes AHB-10).

## Possible future / v2 (deferred)
- [ ] **Job board: advisory `claim_window_seconds` on `post_offer` (AHB-19)** — surfaced in
  the advert / board row / `claim_offer` return so claimants know when the poster intends to
  select; advisory only, no enforcement. Idea from dogfood run #2 (first contested auction),
  noted by avdia 2026-07-12. See AHB-19 for the shape.
- [ ] **DB connection pooling / shared long-lived connection** (deferred 2026-06-18, Workstream 1). Current `db.py` opens a fresh `aiosqlite.connect()` per call (= new thread + WAL handshake each time); throughput tops out ~76 MCP calls/s (100% success, p95 < 1.5s — already past single-user needs). Revisit **only** on multi-user or real throughput pressure. Options weighed: one shared write-conn + read pool (keeps WAL 1-writer/N-reader concurrency) vs a single `asyncio.Lock`-guarded connection (simplest, serializes writes). Not built now to keep the focus on stabilize, not add surface area.
- [ ] Optional auth (FastMCP `TokenVerifier` / static bearer token) + a **uniform `caller_id`** arg for per-tool identity/ownership enforcement (D23/D11) — keep `127.0.0.1` binding regardless.
- [ ] Expire parked **`input_required`** tasks whose clarification is never answered, and **cascade-expire** a stalled clarification's whole exchange (v1 excludes `input_request`/`result` from the `MESSAGE_TTL` sweep — D6/Q3/D24).
- [ ] **`asyncio.Condition`-based long-poll wakeup** (~0 latency) replacing the ~1s async-poll (D21).
- [ ] **Persisted `events` table** + retention/purge for the activity log (v1 is an in-memory ring buffer — D22); general retention/cleanup of old `completed`/`failed`/`expired` rows + session GC — including the one *invisible* growth vector terminal-row GC misses: an abandoned `pending kind='result'` (requester never re-checks its inbox) and a never-read `input_request`, which the D24 carve-out leaves `pending` forever in v1 (no correctness impact — `check_status` stays the durable read; prefer GC/delete over a `state=expired` patch). *(Surfaced in the 2026-06-16 eval triage; kept v2 rather than escalated into D24.)*
- [ ] Stop/AfkStop hook variant that auto-continues an agent's loop on pending work (beyond the basic peek-nudge) — D19, agy `auto_continue_on_max_generator_invocations`.
- [ ] **ACP-derived polish (optional)** — from the 2026-06-16 Agent Client Protocol eval (`mem/acp-evaluation.md`): a **typed cancel/reject `outcome`** for a clarification (sender abandons a parked `input_required` task, vs only answering it — borrowed from ACP `session/request_permission`); a **typed `stop_reason`/fail-category enum** on `failed`/`expired` rows for the dashboard (vs free-text `error`); and **MCP `ContentBlock`** as the shape to adopt *if* multimodal payloads ever matter (v1 keeps `str`). None reopen D1–D25.
- [ ] **Leverage Multi-Agent Framework Concepts (v2 design ready)** — implement features analyzed in [leveraging_multi_agent_frameworks.md](file:///C:/Users/30697/Documents/Projects/mcp-agent-hub-agy/docs/dev/mem/leveraging_multi_agent_frameworks.md):
  - **Git-Native Task Helpers:** Create a script utility to wrap `git diff` and packages task payloads with commit hashes/diff patches.
  - **Dashboard Visual Tracer:** Incorporate Mermaid.js into `index.html` to render session message histories as interactive dependency graphs.
  - **A2A Card API:** Serve spec-compliant A2A `agent-card.json` manifests at `/api/agents/{agent_id}/card` for peer discovery. *(Follow-up — **committed 2026-07-12, DELIVERED same day**: with the repo/docs now public and citable, `wiki-forge` confirmed over the hub ("will do", task 9b7e1691) that it would author the wiki page documenting the "Agent-Card-shape on an MCP hub" pattern via its ingest pipeline — and closed the loop hours later (task 5b1bcc3f): concept page `agent-card-shape-on-mcp-hub` is live in its wiki, grounded in our README + all four docs-site pages (its ingests #320–321). Original context: it offered the page — i.e. an MCP hub borrowing A2A's AgentSkill/Agent-Card shape for discovery without adopting A2A transport/lifecycle — which closes the coverage gap it flagged in the 2026-06-20 `/wiki-serve` MCP-vs-A2A dogfood, where the wiki documents the Hybrid MCP+A2A pattern but not Agent-Cards-grafted-onto-MCP-only.)*
  - **Permission Delegation:** Add support for structured tool permission approval loops via payload extensions.

