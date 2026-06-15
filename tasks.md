# Pending Tasks — MCP Agent Hub

> **This file is the source of truth for what's left to do.** This project travels between two PCs and uses **no local Claude memories** — anything worth preserving lives here (pending work) or in `sessions.md` (history of what's done). Update both in the same change as the work.

## Open design questions (need user sign-off)
- [ ] **Q1 / D2 — Delivery model.** Confirm blocking long-poll `check_inbox` is the primary notification mechanism. Default stands unless vetoed. (See cautionary note re: agents not returning to a *blocking* receive — our long-poll-then-return-empty + documented work-loop is the mitigation.)
- [ ] **Q3 / D6 — Send-to-stale policy.** Confirm: explicit `disconnect` blocks new sends, but mere staleness still queues for the agent's return.
- [ ] **Q5 — Tunable constants.** Accept defaults (`VISIBILITY_TIMEOUT=300s`, `STALE_THRESHOLD=90s`, `LONGPOLL_TIMEOUT=30s`, `DASHBOARD_MESSAGE_LIMIT=100`) or tune.
- [x] **Q6 — Structured capability descriptor.** RESOLVED (2026-06-15, user accepted) → **D16**: Agent-Card `skills[]` on `register_agent`/`list_agents`. Folded into specs/architecture/plan.
- [x] **Q7 — `input_required` state + `session_id` grouping.** RESOLVED (2026-06-15, user accepted) → **D17**: new `input_required` state, 9th tool `request_input`, `session_id`/`parent_id`/`kind` on messages, un-park-on-reply rule. Folded into docs.
- [x] **Q8 — Send-to-stale: flag + surface.** RESOLVED (2026-06-15, user accepted) → **D6 (refined)**: `flagged_stale` on stale sends, surfaced on the dashboard. Folded into docs.
- [x] **Q9 — `Origin`-header validation.** RESOLVED (2026-06-15, user accepted) → **D18**: validate `Origin` on `/mcp` alongside the `127.0.0.1` bind. Folded into docs.

## To verify (not blockers, but confirm before locking)
- [ ] **Antigravity → `localhost` over Streamable HTTP.** `serverUrl` key is confirmed supported; no source demoed it pointed at `http://localhost`. Verify against the real Antigravity CLI during E2E (plan Step 6). This is the one residual caveat on D1.
- [x] **Read [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) source** — done 2026-06-15. Build-from-scratch verdict holds. It's on `fastmcp` 2.x + SQLAlchemy/SQLModel ORM; nests the MCP lifespan manually; instruments via Starlette HTTP middleware + per-tool decorators. **Borrow for `db.py`:** extra WAL pragmas (`synchronous=NORMAL`, `busy_timeout`, passive `wal_checkpoint`) + lightweight retry-on-lock. See `design-decisions.md` Prior Art.
- [x] **Verified `combine_lifespans` import path + `mcp.http_app()` signature** against the real `fastmcp` 3.4.2 source — 2026-06-15 (see `design-decisions.md` § Pre-build API Verification; note the new meta-package / `fastmcp-slim` split). **Residual:** still run `pip freeze` after first install (Step 1) to lock exact patch versions + confirm transitive `starlette`/`uvicorn`/`mcp`.

## Implementation (per plan.md — not started; pre-implementation phase)
- [ ] **Step 1** — venv + `pip install -r requirements.txt` + create directory structure.
- [ ] **Step 2** — `db.py`: `init_db()` (WAL, index), connection helper, registry helpers, message helpers incl. atomic **lazy-on-claim** `claim_pending` (grabs `pending` + stale `in_progress`), `reclaim_stale` backstop. *(Borrow WAL pragmas + retry-on-lock from MCP Agent Mail — see Prior Art.)*
- [ ] **Step 3** — dashboard: `index.html` (Tailwind CDN, status badges incl. **Input Required** + Failed, **⚠ stale-recipient** flag, per-agent **skills**, `session_id` grouping, payload/response/question modal), `/` route, `/api/state` (agents + recent messages capped at `DASHBOARD_MESSAGE_LIMIT`).
- [ ] **Step 4** — `hub.py`: `FastMCP` instance, the cross-cutting `on_call_tool` middleware (`last_seen` + structured logging), the **9 `@mcp.tool`s** (incl. `request_input`; `reply_to_message` un-park rule), mount via `mcp.http_app(path="/mcp")` + `combine_lifespans`, **`Origin`-validation middleware** (D18), lazy reclaim (+ optional asyncio backstop loop), bind `127.0.0.1`.
- [ ] **Step 5** — tests: DB unit tests (atomic claim under concurrency, visibility-timeout redelivery, **skills** JSON round-trip, offline-vs-stale, **`input_required` park/un-park**, **`flagged_stale`**) + scripted MCP smoke test (incl. `request_input` round-trip) via MCP Inspector CLI + an `Origin`-rejection check.
- [ ] **Step 6** — E2E: run server, open dashboard, wire Claude Code (`type:http`) + Antigravity (`serverUrl`), run the haiku cross-agent demo, confirm `check_status` closes the loop.

## Possible future / v2 (deferred)
- [ ] Optional auth (FastMCP `TokenVerifier` / static bearer token) — keep `127.0.0.1` binding regardless. Out of scope for v1 (D11).
