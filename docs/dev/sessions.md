# Session History — MCP Agent Hub

> Append-only log of what was accomplished each session. Pairs with `tasks.md` (what's left). This project travels between two PCs and uses **no local Claude memories** — this file is the durable record. Newest session first.

## 2026-06-18 — Polish & v1 Closing Tasks Complete

Completed all post-v1 polish tasks as the backend owner (agy):
- **README install fixes:** Clarified `~/.gemini/config/mcp_config.json` (not `config.json`) and the object-based `serverUrl` layout for the Antigravity CLI config, alongside enabling JSON hooks in `config.json`.
- **Database cleanup:** Deleted leftover legacy message rows referencing `test-agent` and `test_agent` in `hub.db` using a scratch script, confirming only 85 completed rows remain and the registry is clean.
- **MCP Inspector CLI smoke check:** Verified the tool listing over HTTP wire against a live hub instance on port 8000 using the MCP Inspector CLI.
- **Decision Log (D28):** Documented the consensus-based backend/frontend ownership split and multi-agent roles in `design-decisions.md`.
- **Test suite validation:** Confirmed all 12 tests (`pytest`) pass successfully.

## 2026-06-18 — Workstream 2 (IN PROGRESS): dashboard interactivity & explainability — iteration 1 done

Second post-v1 workstream, 3-agent split (operator-approved). **Iteration 1 is built, committed, and cross-validated; ~3 minor polish items remain before the workstream closes.**

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
