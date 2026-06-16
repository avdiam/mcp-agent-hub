# Session History — MCP Agent Hub

> Append-only log of what was accomplished each session. Pairs with `tasks.md` (what's left). This project travels between two PCs and uses **no local Claude memories** — this file is the durable record. Newest session first.

## 2026-06-16 — Pre-build evaluation-report triage (adopted `ruff`; reverted a premature v1 TTL escalation)

Triaged an independent pre-build evaluation report (Gemini, `evaluation_report.md`) against the locked design. Still pre-implementation; no app code.

**Triage outcome:**
- **Adopted:** `ruff>=0.4` into `requirements.txt` dev deps (fast lint/format from day one) — the one clearly-new, low-cost suggestion.
- **Declined:** `structlog` (cuts against the zero-extra-deps ethos — stdlib `logging`+JSON already satisfies D22); payload as `dict`/`Any` (misreads the domain — agent messages are prose, `str` is the right primitive; JSON-stringify by convention if structure is ever needed); terminal-row DB GC and a `sessions` table (already a known v2 item / deliberately out of scope).
- **Surfaced (sharper than the report):** the one *invisible* unbounded-growth vector the report's terminal-row-GC idea misses — a `pending kind='result'` whose requester never re-checks its inbox (plus a never-read `input_request`) escapes claim, reclaim, **and** the D24 TTL carve-out, so it sits `pending` forever. **Low severity** (localhost, slow, no correctness impact — `check_status` is the durable read).

**Course-correction:** a first pass *prematurely escalated* that v2-grade finding into a v1 edit of locked decision **D24** (extending the TTL sweep to `kind='result'`) and applied it **incompletely** — leaving `architecture.md` (×2), the `design-decisions.md` D6 row, and a `tasks.md` v2 bullet still asserting the original "task-only / result-excluded" behavior (4 internal contradictions; the "perfectly synced/locked" claim was therefore wrong). **Reverted** the D24 / `specs.md` / `plan.md` / `tasks.md` wording back to "`kind='task'` only" so D1–D25 stay exactly as signed off, and **folded the result-row vector into the existing v2 retention/GC workstream** (`tasks.md`) — handle all row-growth together there, preferring GC/*delete* over a `state=expired` patch (which would mislabel a delivered-elsewhere notification as "Expired" on the dashboard and slightly narrow D20 for senders absent >24h). Kept `ruff`.

**Files changed:** `requirements.txt` (+`ruff`, kept), `tasks.md` (v2 retention bullet now names the abandoned-`result`/`input_request` vector; D24 bullet + Step 2 reverted), `design-decisions.md` / `specs.md` / `plan.md` (D24 sweep wording reverted to `kind='task'` only), `sessions.md` (this entry, replacing the earlier premature one). `architecture.md` needed no edit — the revert restored consistency with it.

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
