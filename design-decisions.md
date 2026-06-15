# MCP Agent Hub — Design Decisions & Open Questions

This addendum records the decisions folded into `project-purpose.md`, `specs.md`, `architecture.md`, and `plan.md` during the pre-build review — the rationale for each, the alternatives considered, and the handful of choices that still want your explicit sign-off.

Each item in the Decision Log is the **current default baked into the docs**. Veto any of them and the docs get revised.

## Decision Log

| # | Decision | Rationale | Alternative (rejected) |
|---|----------|-----------|------------------------|
| D1 | **Transport: Streamable HTTP** at a single `/mcp` endpoint — not HTTP+SSE | The HTTP+SSE two-endpoint transport was deprecated in MCP spec `2025-03-26`; current clients and FastMCP default to Streamable HTTP. **Confirmed (June 2026):** both Claude Code (`"type": "http"`) and Antigravity (`serverUrl` key) speak Streamable HTTP, so a single `/mcp` endpoint serves both — see Q4 | Keep legacy SSE (`/sse` + `/messages`) — only justified for an old client that can't upgrade |
| D2 | **Delivery: blocking long-poll `check_inbox(wait, timeout)`** plus a documented agent work-loop | CLI agents have no inbound port and can't be pushed to; long-poll avoids both spin-polling and human-in-the-loop nudging | Pure client-side polling loop (token-costly); external out-of-band nudge (more moving parts) |
| D3 | **At-least-once + visibility timeout**; `reply`/`fail` act as the ack; unacked `in_progress` is redelivered | Makes the "resilience" goal real — a crashed worker's task is recovered; standard queue pattern | At-most-once (silently loses crashed work) |
| D4 | **`check_inbox` claims atomically** via `UPDATE ... RETURNING` | Prevents two concurrent callers double-claiming the same message | `SELECT` then `UPDATE` — racy |
| D5 | **Liveness: refresh `last_seen` on every call; "stale" = silent past threshold; "offline" only via explicit `disconnect`** | Distinguishes "busy/restarting" from "done", so queued work survives a restart | Hard-offline on staleness — would drop recoverable work |
| D6 | **`send_message` rejects unknown / explicitly-disconnected recipients; queues to known-but-stale ones** | Balances spec §2's "no sends to offline agents" against the resilience goal | Reject all stale (hurts restart recovery); accept all incl. unknown (messages rot) |
| D7 | **8 tools** — added `fail_message`, kept `check_status` | Completes the `pending → failed` branch; `check_status` is required for the sender's poll-for-response | Merge fail into `reply_to_message` via a status param — less explicit |
| D8 | **Dashboard at `/`, data at `/api/state`, MCP at `/mcp`** | One consistent set of paths (the docs previously split `/ui` vs `/`) | Serve UI at `/ui` |
| D9 | **SQLite in WAL mode + DB calls run off the event loop** | Avoids "database is locked" and event-loop stalls from blocking I/O in async handlers | Default rollback journal + synchronous calls in async routes |
| D10 | **`capabilities` stored as JSON text** | SQLite has no native array type | Comma-joined string — fragile |
| D11 | **No auth; bind `127.0.0.1`; trust the supplied `agent_id`** | Single-user local dev tool; auth is disproportionate for v1 | Token auth now — deferred to a possible v2 |
| D12 | **Add DB unit tests + a scripted MCP smoke test** | The stated "reliability" goal needs automated coverage, not just manual E2E | Manual-only testing |
| D13 | **Depend on the standalone `fastmcp` 3.x** (`>=3.4,<4`), not the official SDK's bundled `mcp.server.fastmcp` | 3.x has the first-class FastAPI mount story (`mcp.http_app(path=...)` + lifespan forwarding); the official SDK still has open friction mounting Streamable HTTP under FastAPI (python-sdk #1367). Let `fastmcp` resolve `starlette`/`uvicorn`/`mcp` transitively — 3.4.1 floors `starlette>=1.0.1` for CVE-2026-48710, so a self-pinned older Starlette would conflict | Standalone `fastmcp` 2.x (older mount API); official `mcp` SDK (mount friction) — see Q2 |
| D14 | **One FastMCP middleware (`on_call_tool`) centralizes `last_seen` refresh + structured per-call logging** | Removes that boilerplate from all 8 tools and gives the dashboard a uniform event stream; identity comes from the `agent_id` tool arg (no auth in v1) | Repeat the `touch_last_seen` + log call inside every tool — error-prone, drifts |
| D15 | **Visibility-timeout reclaim is lazy-on-claim** — the atomic claim query also grabs `in_progress` rows whose `claimed_at < now - VISIBILITY_TIMEOUT`; an optional `asyncio` loop in the lifespan is only a backstop | Simplest robust option, zero moving parts, exactly the SQS pattern; reclaim happens naturally on the next poll | APScheduler (persistence/extra-dep overkill for a localhost tool); rely solely on a periodic sweep |

## Tunable Constants (proposed defaults — confirm)

| Constant | Default | Effect of changing |
|----------|---------|--------------------|
| `VISIBILITY_TIMEOUT` | `300s` | Time before an unacked `in_progress` message reverts to `pending`. Lower = faster redelivery after a crash, but more duplicates for genuinely slow tasks. |
| `STALE_THRESHOLD` | `90s` | How long an agent can be silent before the dashboard marks it "stale". With long-poll, an on-duty agent re-calls `check_inbox` within its poll timeout and stays fresh. |
| `LONGPOLL_TIMEOUT` (default) | `30s` | Max time a single `check_inbox(wait=true)` call blocks before returning empty. |
| `DASHBOARD_MESSAGE_LIMIT` | `100` | Rows returned by `/api/state` (caps unbounded growth of the response). |

## Open Questions — want your sign-off before build

1. **Delivery model (D2).** OK to make a blocking long-poll `check_inbox` the primary "knock on the door", or do you prefer a different notification strategy? This shapes the `check_inbox` tool contract, so it's worth settling first. *Note: a tiny analogue ([postal-mcp](https://github.com/tkellogg/postal-mcp)) reports that with a purely **blocking** receive "Claude Code doesn't return to the mailbox easily." Our long-poll-then-return-empty contract + the documented work-loop is the mitigation — keep the loop prominent in the agent-facing docs.*
2. ~~**Which MCP package.**~~ **RESOLVED → D13:** standalone **`fastmcp` 3.x** (`>=3.4,<4`). Mount via `mcp.http_app(path="/mcp")` and forward its lifespan into FastAPI.
3. **Send-to-stale policy (D6).** Agreed that an **explicit disconnect** blocks new sends, but mere **staleness** still queues the message for when the agent returns?
4. ~~**Antigravity transport.**~~ **RESOLVED (with one smoke-test caveat) → D1:** Antigravity supports remote Streamable HTTP via the `serverUrl` key (config at `~/.gemini/antigravity/mcp_config.json`; Windows `C:\Users\<you>\.gemini\antigravity\mcp_config.json`). No source explicitly demoed `serverUrl` → `localhost`, so **verify against the real Antigravity CLI during E2E (plan Step 6) before fully locking D1.**
5. **Constants.** Accept the four defaults above, or tune any of them?

## Prior Art & Dev Tooling (reference)

- **[MCP Agent Mail](https://github.com/Dicklesworthstone/mcp_agent_mail)** (~2k★, active) — same stack (FastMCP + SQLite + dashboard). It's an **ack-based mailbox**, not our at-least-once + visibility-timeout queue (D3), so we **build from scratch** but borrow its FastMCP mount wiring and dashboard patterns. Worth reading before writing `hub.py`.
- **[A2A](https://a2a-protocol.org/latest/)** reached v1.0 but is the wrong topology for us (agents-as-peer-servers, no durable central queue) and our CLI clients are MCP-native — reconsidered and rejected as the transport; its Agent Card discovery is a useful reference for the `list_agents` payload.
- **Testing:** MCP Inspector CLI for the scripted smoke test — `npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list`. Verify the client side with `claude mcp list` / the `/mcp` panel in Claude Code.
- **Reference scaffolding:** the official `mcp-server-dev` Claude Code plugin; Context7 for live FastMCP docs.
