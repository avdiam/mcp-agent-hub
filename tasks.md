# Pending Tasks — MCP Agent Hub

> **This file is the source of truth for what's left to do.** This project travels between two PCs and uses **no local Claude memories** — anything worth preserving lives here (pending work) or in `sessions.md` (history of what's done). Update both in the same change as the work.

## Open design questions (need user sign-off)
- [ ] **Q1 / D2 — Delivery model.** Confirm blocking long-poll `check_inbox` is the primary notification mechanism. Default stands unless vetoed. (See cautionary note re: agents not returning to a *blocking* receive — our long-poll-then-return-empty + documented work-loop is the mitigation.)
- [ ] **Q3 / D6 — Send-to-stale policy.** Confirm: explicit `disconnect` blocks new sends, but mere staleness still queues for the agent's return.
- [ ] **Q5 — Tunable constants.** Accept defaults (`VISIBILITY_TIMEOUT=300s`, `STALE_THRESHOLD=90s`, `LONGPOLL_TIMEOUT=30s`, `DASHBOARD_MESSAGE_LIMIT=100`) or tune.

## To verify (not blockers, but confirm before locking)
- [ ] **Antigravity → `localhost` over Streamable HTTP.** `serverUrl` key is confirmed supported; no source demoed it pointed at `http://localhost`. Verify against the real Antigravity CLI during E2E (plan Step 6). This is the one residual caveat on D1.
- [ ] **Read [MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail) source** (~30 min) before writing `hub.py` — same stack (FastMCP + SQLite + dashboard); borrow its mount wiring + dashboard patterns. We build from scratch (it's ack-based mailbox, not our at-least-once + visibility-timeout queue).
- [ ] Confirm exact installed versions after first `pip install -r requirements.txt` (`pip freeze`) — verify `combine_lifespans` import path and `mcp.http_app()` signature against the pinned `fastmcp` 3.x.

## Implementation (per plan.md — not started; pre-implementation phase)
- [ ] **Step 1** — venv + `pip install -r requirements.txt` + create directory structure.
- [ ] **Step 2** — `db.py`: `init_db()` (WAL, index), connection helper, registry helpers, message helpers incl. atomic **lazy-on-claim** `claim_pending` (grabs `pending` + stale `in_progress`), `reclaim_stale` backstop.
- [ ] **Step 3** — dashboard: `index.html` (Tailwind CDN, status badges incl. Failed, payload modal), `/` route, `/api/state` (agents + recent messages capped at `DASHBOARD_MESSAGE_LIMIT`).
- [ ] **Step 4** — `hub.py`: `FastMCP` instance, the cross-cutting `on_call_tool` middleware (`last_seen` + structured logging), the 8 `@mcp.tool`s, mount via `mcp.http_app(path="/mcp")` + `combine_lifespans`, lazy reclaim (+ optional asyncio backstop loop), bind `127.0.0.1`.
- [ ] **Step 5** — tests: DB unit tests (atomic claim under concurrency, visibility-timeout redelivery, capabilities JSON round-trip, offline-vs-stale) + scripted MCP smoke test via MCP Inspector CLI.
- [ ] **Step 6** — E2E: run server, open dashboard, wire Claude Code (`type:http`) + Antigravity (`serverUrl`), run the haiku cross-agent demo, confirm `check_status` closes the loop.

## Possible future / v2 (deferred)
- [ ] Optional auth (FastMCP `TokenVerifier` / static bearer token) — keep `127.0.0.1` binding regardless. Out of scope for v1 (D11).
