# Session History — MCP Agent Hub

> Append-only log of what was accomplished each session. Pairs with `tasks.md` (what's left). This project travels between two PCs and uses **no local Claude memories** — this file is the durable record. Newest session first.

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
