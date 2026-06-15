# Pending Tasks — MCP Agent Hub

> **This file is the source of truth for what's left to do.** This project travels between two PCs and uses **no local Claude memories** — anything worth preserving lives here (pending work) or in `sessions.md` (history of what's done). Update both in the same change as the work.

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
- [x] **Verified `combine_lifespans` import path + `mcp.http_app()` signature** against the real `fastmcp` 3.4.2 source — 2026-06-15 (see `design-decisions.md` § Pre-build API Verification; note the new meta-package / `fastmcp-slim` split). **Residual:** still run `pip freeze` after first install (Step 1) to lock exact patch versions + confirm transitive `starlette`/`uvicorn`/`mcp`.

## Implementation (per plan.md — not started; pre-implementation phase)

Build in **phases** (D25): **P1** skeleton + green haiku E2E → **P2** skills/`input_required`/result-to-inbox → **P3** expiry/`flagged_stale`/Origin → **P4** hook layer. Structural columns ship in P1.

- [ ] **Step 1 (P1)** — venv + `pip install -r requirements.txt` (+ dev `pytest`/`httpx`) + **`pip freeze`** + create directory structure (incl. `hook_peek.py`). **Context7** re-verify the FastMCP 3.x API + FastAPI/uvicorn; optional `mcp-server-dev` scaffold.
- [ ] **Step 2 (P1+)** — `db.py`: `init_db()` (WAL, indexes incl. `created_at`, **assert SQLite ≥3.35**), `aiosqlite` connection helper (D21), registry helpers, message helpers incl. atomic **lazy-on-claim** `claim_pending` (grabs `pending` + stale `in_progress`; excludes parked `input_required`; **auto-completes `kind='result'` on claim — D20**), `reclaim_stale` backstop, **`peek_inbox`** (read-only — D19), **`expire_messages`** (`pending kind='task'`→`expired` — D6/Q3/D24), and `complete_message` with the **result fan-out (D20)** + **un-park (D17)** rules. Status enum includes **`expired`**; `kind` includes **`result`**. *(Borrow WAL pragmas + retry-on-lock from MCP Agent Mail.)*
- [ ] **Step 3 (P1, enrich P2+)** — dashboard via **`frontend-design`**: `index.html` (Tailwind CDN, status badges incl. **Input Required**/**Failed**/**Expired**, **`kind`** indicator incl. **result**, **⚠ stale-recipient** flag, per-agent **skills**, `session_id` grouping, payload/response/question modal, **Activity panel — D22**), `/` route, `/api/state` (agents + recent messages + **events** + stats), read-only **`/api/peek`** (D19).
- [ ] **Step 4 (P1, enrich P2–P4)** — `hub.py`: `FastMCP` instance, cross-cutting `on_call_tool` middleware (**`last_seen` from direct actor arg — D23** + **in-memory activity ring buffer — D22**), the **9 `@mcp.tool`s** (docstrings as first-class deliverable; `check_inbox` **`wait=True` async-poll — D21**; `reply_to_message` **result fan-out — D20** + un-park; `register_agent` Pydantic **`Skill`** — D16), mount via `mcp.http_app(path="/mcp")` + `combine_lifespans`, **`Origin`-validation middleware** (D18), lazy reclaim + optional asyncio backstop running **`reclaim_stale` + `expire_messages`** (D15/D24), bind `127.0.0.1`.
- [ ] **Step 5** — tests: DB unit tests (atomic claim under concurrency, visibility redelivery, **skills** JSON round-trip, offline-vs-stale, **`input_required` park/un-park**, **`flagged_stale`**, **`expired` sweep**, **`peek_inbox` non-mutating**, **result fan-out — D20**) via in-memory **`fastmcp.Client`** + one real-HTTP check (Inspector CLI) + `Origin`-rejection + **`/api/peek`** check. **`/code-review`** each phase's diff.
- [ ] **Step 6** — E2E via **`/run`** + **`/verify`**: run server, open dashboard, wire Claude Code (`type:http`) + Antigravity (`serverUrl` in `~/.gemini/config/mcp_config.json` — verified), run the haiku cross-agent demo (P1; requester reads result via `check_status`, then via its inbox once D20 lands), confirm the loop; then **wire the D19 hook layer** (P4: `hook_peek.py` into Claude Code `Stop`/`UserPromptSubmit` + agy `StopHook`/`PreInvocationHook`) and confirm the peek-nudge path. **`/security-review`** after P3.

## Possible future / v2 (deferred)
- [ ] Optional auth (FastMCP `TokenVerifier` / static bearer token) + a **uniform `caller_id`** arg for per-tool identity/ownership enforcement (D23/D11) — keep `127.0.0.1` binding regardless.
- [ ] Expire parked **`input_required`** tasks whose clarification is never answered, and **cascade-expire** a stalled clarification's whole exchange (v1 excludes `input_request`/`result` from the `MESSAGE_TTL` sweep — D6/Q3/D24).
- [ ] **`asyncio.Condition`-based long-poll wakeup** (~0 latency) replacing the ~1s async-poll (D21).
- [ ] **Persisted `events` table** + retention/purge for the activity log (v1 is an in-memory ring buffer — D22); general retention/cleanup of old `completed`/`failed`/`expired` rows + session GC.
- [ ] Stop/AfkStop hook variant that auto-continues an agent's loop on pending work (beyond the basic peek-nudge) — D19, agy `auto_continue_on_max_generator_invocations`.
