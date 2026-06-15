# Session History — MCP Agent Hub

> Append-only log of what was accomplished each session. Pairs with `tasks.md` (what's left). This project travels between two PCs and uses **no local Claude memories** — this file is the durable record. Newest session first.

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
