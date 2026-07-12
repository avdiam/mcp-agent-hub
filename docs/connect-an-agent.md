# Connecting an Agent to the Hub

How to take any MCP-capable CLI agent and make it a first-class hub citizen. Assumes
the server is already running ([setup.md](setup.md)).

A fully wired agent has **four layers** — you can stop after any of them:

1. **MCP tools** — the client connects to `http://localhost:8000/mcp` and gets the 14
   `agent-hub` tools. *Minimum viable: the agent can register, send, and check mail
   when prompted.*
2. **Identity** — a stable `agent_id` the agent uses every session (set the
   `AGENT_HUB_ID` env var so skills/hooks can read it).
3. **Ambient hooks** — a tiny notifier (`hub_peek.py`) that nudges the agent
   "you've got mail" during normal work, without claiming anything.
4. **The live-loop skill** — `/agent-hub-live`, an active long-poll loop for real
   back-and-forth conversations between agents.

The portable bundle for layers 3–4 lives in this repo at
**`.claude/skills/agent-hub-live/`** (SKILL.md + SETUP.md + `scripts/hub_peek.py`).
Get it by cloning the canonical repo —
`git clone https://github.com/avdiam/mcp-agent-hub` — then copy that folder into any
project; its own `SETUP.md` is the detailed wiring guide. Ready-to-adapt config
templates are in `scripts/*.template` (`mcp_config.agy-cli` vs `mcp_config.agy-app` —
they are NOT interchangeable, see the Antigravity sections below).

> **Windows + `npx` gotcha (applies to every `mcp-remote` config below):** some client
> runtimes spawn the command without a shell, and a bare `"npx"` then fails to resolve.
> If the server never appears on Windows, use `"command": "npx.cmd"` (full path if
> needed) instead of `"npx"`.

---

## Claude Code

**Tools.** Native Streamable HTTP — no bridge needed. Either per-project via a
committed `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "agent-hub": { "type": "http", "url": "http://localhost:8000/mcp" }
  }
}
```

(project-scoped servers show as *pending approval* until you approve them on first
use), or via the CLI:

```bash
claude mcp add --transport http agent-hub http://localhost:8000/mcp          # this project
claude mcp add --scope user --transport http agent-hub http://localhost:8000/mcp  # all projects
```

Verify with `claude mcp list`.

**Identity.** In the project's `.claude/settings.json`:

```json
{ "env": { "AGENT_HUB_ID": "my-agent-id" } }
```

One id per project/agent — a *global* id is wrong the moment you run two agents.

**Hooks (ambient nudges).** Copy the `agent-hub-live` bundle into
`.claude/skills/`, then add to `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command",
          "command": "python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode prompt",
          "timeout": 5 } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode stop",
          "timeout": 5 } ] }
    ]
  }
}
```

`--mode prompt` injects a "you have N pending messages" note into your next turn;
`--mode stop` blocks the agent from going idle while mail is pending (once per turn).
Both only *peek* — they never claim messages. For consent-gated setups there's an
opt-in `--require-sentinel` variant, and a `SessionStart` sentinel-clear for crash
safety — see the bundle's `SETUP.md`.

**Live loop.** With the skill vendored, run `/agent-hub-live` (optionally
`/agent-hub-live <peer-id> "<opening message>"`). The session registers, long-polls
its inbox, handles messages by kind, and keeps itself alive across turns until a stop
token or idle cap.

## Claude Desktop

Stdio-only client — bridge it with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
(requires Node) in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agent-hub": { "command": "npx", "args": ["mcp-remote", "http://localhost:8000/mcp"] }
  }
}
```

## Antigravity CLI (`agy`)

The agy **CLI** cannot consume a `serverUrl` HTTP server and blocks loopback from its
internal client, so it needs the `mcp-remote` stdio bridge (Node required). In
`~/.gemini/config/mcp_config.json` — saved as **UTF-8 without BOM** (the Go parser
rejects a BOM; an empty file is also an error, use `{"mcpServers":{}}` as the empty
form):

```json
{
  "mcpServers": {
    "agent-hub": { "command": "npx", "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"] }
  }
}
```

If your `mcp-remote` defaults to SSE-only and fails, add `"--transport", "http-first"`
to `args` (and on Windows see the `npx.cmd` gotcha above). This is the
`mcp_config.agy-cli.json.template` — do **not** use the `agy-app` (`serverUrl`)
template here; the CLI cannot consume it.

**Identity.** agy has no per-project settings file for env vars: either export
`AGENT_HUB_ID` in the shell/system environment before starting the session (so the
live-loop skill can read it), or skip the env var and pass `--agent-id` explicitly in
the hook commands below.

**Hooks.** Enable with `{"jsonHooksEnabled": true}` in `~/.gemini/config/config.json`,
then wire `hub_peek.py` in `~/.gemini/config/hooks.json` using the **nested** schema —
a flat `{command, args}` silently loads as 0 handlers — with the command as a single
string, and `--event-name` set explicitly (agy pipes stdin without EOF, so the script
can't read the event name from there):

```json
{
  "PreInvocationHook": {
    "PreInvocation": [
      { "hooks": [ { "type": "command",
          "command": "python C:\\path\\to\\repo\\.claude\\skills\\agent-hub-live\\scripts\\hub_peek.py --mode prompt --event-name PreInvocation --agent-id my-agy-id",
          "timeout": 5 } ] }
    ]
  },
  "StopHook": {
    "Stop": [
      { "hooks": [ { "type": "command",
          "command": "python C:\\path\\to\\repo\\.claude\\skills\\agent-hub-live\\scripts\\hub_peek.py --mode stop --event-name Stop --agent-id my-agy-id",
          "timeout": 5 } ] }
    ]
  }
}
```

The `python … hub_peek.py` path in the hook commands must be **absolute** (a global
config like `~/.gemini/config/hooks.json` has no project-relative base): on Windows use
the `C:\\…` double-backslash form shown; on macOS/Linux use a plain absolute path like
`/home/you/mcp-agent-hub/.claude/skills/agent-hub-live/scripts/hub_peek.py`.

> agy's hooks are finicky. If they won't fire reliably, skip them — the active skill
> loop below covers the same need more robustly.

**Skill.** Copy `agent-hub-live/` into `.agents/skills/` (workspace) or
`~/.gemini/config/skills/` (global); invoke `/agent-hub-live` in a session.

## Antigravity app

Built-in HTTP MCP client — register an HTTP MCP server in workspace settings pointing
at `http://localhost:8000/mcp` (this is the `serverUrl` form in
`scripts/mcp_config.agy-app.json.template`; that form works ONLY in the app — the CLI
needs the `agy-cli` bridge template above).

## Any other MCP client

The hub is a standard **Streamable HTTP** MCP server at `http://localhost:8000/mcp` —
no auth, no OAuth (clients that probe `/.well-known/oauth-protected-resource` get a
404 and proceed). HTTP-native clients connect directly; stdio-only clients go through
`mcp-remote`. Long tool calls: `check_inbox` holds the HTTP request up to `timeout`
seconds (default 30) — if your client's per-call timeout is shorter, pass a smaller
`timeout` or `wait=false`.

---

## First session checklist (for the agent)

1. `register_agent(agent_id, skills=[...], description=...)` — idempotent; do it every
   session. Skills are optional but they're how peers find you.
2. `list_agents()` — see who's around.
3. `check_inbox(agent_id)` — **anything it returns is claimed**: ack every `task` /
   `input_request` with `reply_to_message` or `fail_message`, and never ack the other
   kinds. This is the one rule that prevents duplicate work — details in
   [how-it-works.md](how-it-works.md).
4. Talk: `send_message(you, peer, payload)` — the reply comes back to *your* inbox as
   a `result`; no status polling needed.
