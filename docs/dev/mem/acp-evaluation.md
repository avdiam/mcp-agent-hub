# Agent Client Protocol (ACP) — evaluation for MCP Agent Hub

> Research note (2026-06-16). Should we use Zed's **Agent Client Protocol (ACP)** —
> directly, alongside our MCP server, or as inspiration? **Verdict: not as transport
> (wrong topology); already coexists with us for free at the MCP layer; worth a few
> validations + one optional v2 refinement.** Nothing here reopens D1–D25.

## ⚠ First: there are TWO "ACP"s — don't conflate them

| Name | Owner | Shape | Status | Where it shows up |
|------|-------|-------|--------|-------------------|
| **Agent _Communication_ Protocol (ACP)** | IBM / BeeAI | REST-native **agent↔agent** | Donated to LF, **merged into A2A Sept 2025; winding down** | The "merged with IBM's ACP" in our existing A2A survey bullet |
| **Agent _Client_ Protocol (ACP)** | Zed Industries | **editor↔agent** ("LSP for coding agents") | Active; Zed+JetBrains registry (Jan 2026) | **This note** — never previously evaluated |

Our `design-decisions.md` survey already referenced the **Communication** one. The
**Client** one (this note) is a genuinely new input.

## What the Agent Client Protocol is

"A protocol for connecting any editor to any agent" — the LSP analogy for coding agents
(turns M×N editor↔agent integrations into M+N).

- **Roles:** **Client = the editor/IDE** (Zed, JetBrains, neovim, Marimo, Kiro).
  **Agent = the coding-agent subprocess** (Gemini CLI is the reference impl; Claude Code
  via an adapter; Codex, Goose, Cline…).
- **Transport:** JSON-RPC 2.0 over **stdio**, agent run as a **subprocess of the editor**.
  Remote (HTTP/WebSocket) is "work in progress."
- **Methods:** `initialize` (capabilities + integer `protocolVersion`), `authenticate`,
  `session/new` (`cwd` + `mcpServers` → `sessionId`), `session/load` (replays the whole
  conversation via updates), `session/prompt` (content blocks), `session/update`
  (streamed: `agent_message_chunk`, `agent_thought_chunk`, `plan`, `tool_call`,
  `tool_call_update`, `usage_update`, `current_mode_update`…), `session/request_permission`,
  `session/cancel`, plus **client-provided** `fs/read_text_file`, `fs/write_text_file`,
  `terminal/*`. Stop reasons: `end_turn`, `max_tokens`, `max_turn_requests`, `refusal`,
  `cancelled`.
- **Relation to MCP (key):** ACP **layers on top of MCP**. The editor tells the agent which
  MCP servers to connect to via `session/new`'s `mcpServers` (stdio required; **http + sse
  optional**); the agent then acts as an MCP **client** of those servers. ACP reuses MCP's
  JSON content-block types. So **ACP = editor↔agent; MCP = agent↔tools** — orthogonal layers,
  not competitors.
- SDKs: Rust / TypeScript / Python / Java / Kotlin, Apache-2.0.

## Q1 — Use it directly as our mechanism? **No.**

Wrong shape, decisively (sharper version of why we rejected A2A-as-transport):

- **Topology:** ACP is **vertical** (1 editor → 1 agent, hierarchical). We are **horizontal**
  (N peer agents ↔ central broker). ACP has **no** concept of a recipient, a second agent,
  a peer registry, or an inbox. Sibling agents under one editor never see each other.
- **Transport:** ACP local agents are **stdio subprocesses the editor spawns**. Our entire
  reason to exist is that CLI agents have **no inbound port** and need a long-lived HTTP
  broker between *already-running, independent* processes. ACP remote transport is WIP.
- **Role inversion:** to push agent↔agent traffic through ACP, the hub would have to
  **masquerade as an "editor" and spawn each agent** — exactly the lifecycle ownership we
  avoid (we broker between agents the user launched themselves).
- **Clincher:** in ACP, **Claude Code and the Gemini-family CLIs are the _Agents_** (Gemini
  CLI is the reference agent; Claude Code ships via an adapter). ACP positions our exact
  target clients as *things an editor drives* — and gives them **no path to reach each other.**

## Q2 — Use it _with_ our MCP server? **Yes — nothing to build.**

They already coexist by design, and it validates D1 (http transport):

- The hub **is** an MCP server (Streamable HTTP). ACP's `session/new` accepts **http MCP
  servers** in `mcpServers`.
- So: a user running Claude Code / Gemini CLI **inside an ACP editor (e.g. Zed)** can point
  that agent at the hub by adding it to the **editor's** `mcpServers` list — the **same
  `{"type":"http","url":".../mcp"}`** config we already document. The agent joins the hub as
  a peer through its normal MCP client path. **Zero ACP-specific code in the hub.**
- ACP sits *above* us (editor↔agent); the hub lives in the MCP layer *beneath*. Do **not**
  make the hub an ACP client/server — buys nothing, fights the topology.

## Q3 — Inspiration to adopt/adapt? Validations + 1 optional v2 refinement.

1. **`session/request_permission` ≈ our D17 `input_required`/`request_input`** (strongest).
   Both = "agent pauses mid-task, asks back up the channel, the answer un-blocks it." Makes
   ACP a **4th converging data point** for D17 (alongside A2A `input-required`, LangGraph
   `thread_id`, CrewAI flow id). Difference: ACP models the answer as **typed
   `PermissionOption`s + an `outcome` enum** (allow-once / allow-always / reject;
   `selected` vs `cancelled`), not free text. Keep our free-text prose for v1 (fits
   "no special client support"). **Optional v2:** if we ever want a cancel/reject path for a
   clarification (sender abandons the task), ACP's `cancelled` outcome is the clean model.
2. **`tool_call` → `tool_call_update` status lifecycle** (pending→in_progress→
   completed/failed/cancelled) mirrors our message state machine ~1:1 — validates it as idiomatic.
3. **Typed `stop_reason` enum** vs our free-text `fail_message(error)`. Gold-plating for v1
   (our `failed`/`expired`/`completed` + free-text error suffices); a reasonable v2 dashboard polish.
4. **Shared MCP/ACP `ContentBlock`** (text/image/audio/resource) vs our deliberate `str`
   payload (eval triage 2026-06-16). Doesn't change v1 (`str` is right for prose between
   coding agents) — but if multimodal ever matters, **MCP `ContentBlock` is the
   cross-ecosystem standard to adopt**, not a bespoke shape. Reinforces the existing decision.
5. **Positioning (genuinely useful):** the hub is the **horizontal, durable, local peer
   complement to ACP's vertical editor↔agent standard.** 2026 stack: MCP = agent↔tools;
   ACP = editor↔agent; A2A = agent↔agent over the network; **MCP Agent Hub = agent↔agent,
   local, durable, brokered.**

## Sources (primary, fetched 2026-06-16)

- Zed ACP: https://zed.dev/acp · Introduction: https://agentclientprotocol.com/get-started/introduction
- Initialization / session-setup / prompt-turn: https://agentclientprotocol.com/protocol/session-setup
- Claude Code via ACP: https://zed.dev/blog/claude-code-via-acp · External agents: https://zed.dev/docs/ai/external-agents
- IBM ACP → A2A merger: https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/
