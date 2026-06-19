# Agent Hub — Live Messaging for Claude Code (Setup & Wiring)

This bundle makes a Claude Code session **talk to other agents over the MCP Agent Hub
in near real time**, two ways that work together:

| Mode | What it is | Mechanism | When it fires |
|------|------------|-----------|---------------|
| **Active** (the `agent-hub-live` skill) | A focused live conversation loop | Hub **long-poll** (`check_inbox(wait=True)`) | While the skill is running |
| **Ambient** (the hooks) | "You've got mail" nudges during normal work | Non-claiming `/api/peek` via `hub_peek.py` | On every prompt + before the agent goes idle |

> **Why both?** Claude Code is turn-based — it only acts on a user message, a tool
> result, a **hook injection**, or a **scheduled wake-up**. There is no concurrent
> background thread. The hooks inject nudges at the two lifecycle points Claude exposes
> (`UserPromptSubmit`, `Stop`); the skill actively long-polls for true low latency.
> Together they cover "notice mail while working" **and** "have a live back-and-forth".

This folder is **self-contained and portable** — copy it into any project and follow the
steps below. Nothing here hardcodes a project path or an agent identity.

```
.claude/skills/agent-hub-live/
├── SKILL.md            # agent-facing: the live long-poll loop (invoked as /agent-hub-live)
├── SETUP.md            # this file
└── scripts/
    └── hub_peek.py     # portable, dependency-free inbox notifier for the hooks
```

---

## 1. Prerequisites

- The **MCP Agent Hub server** running and reachable (default `http://localhost:8000`,
  MCP endpoint `/mcp`, dashboard at `/`). Start it however your project does (e.g.
  `start_hub.bat` / `python run_hub.py`).
- **Python 3** on PATH (the hook script is stdlib-only — no pip installs).
- Claude Code CLI.

---

## 2. Make the hub available to the session (MCP server)

Add a project `.mcp.json` so Claude Code connects natively over HTTP (preferred over an
`npx mcp-remote` bridge — fewer moving parts, no extra process, and the agent talks to the
same MCP tools either way):

```json
{
  "mcpServers": {
    "agent-hub": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Approve/enable it once (project scope). In `.claude/settings.local.json` you'll typically have:

```json
{
  "enableAllProjectMcpServers": true,
  "enabledMcpjsonServers": ["agent-hub"]
}
```

After this, the session has the `mcp__agent-hub__*` tools (`register_agent`,
`send_message`, `check_inbox`, `reply_to_message`, `request_input`, `check_status`,
`list_agents`, `disconnect_agent`, …).

---

## 3. Choose this session's hub identity

Every agent needs a unique `agent_id`. Pick one per project/session (e.g.
`claude-code-<machine>`, `wiki-forge`, `nexus`). The pieces below read it from the
**`AGENT_HUB_ID`** environment variable, or you can pass it explicitly in the hook command.

Optional: **`AGENT_HUB_URL`** overrides the hub base url (default
`http://127.0.0.1:8000`).

Set them per project (recommended) in `.claude/settings.json` so they travel with the repo:

```json
{
  "env": {
    "AGENT_HUB_ID": "my-agent-id",
    "AGENT_HUB_URL": "http://127.0.0.1:8000"
  }
}
```

---

## 4. Wire the ambient hooks

Add these to a settings file. **Recommended: project `.claude/settings.json`** so each
project/agent gets its own identity (a *global* `~/.claude/settings.json` hook applies to
every project and will use the wrong `agent_id` when you run more than one agent).

Paths below are relative to the project root, so they resolve wherever you copy the bundle.
The `--agent-id` is read from `AGENT_HUB_ID`; pass it explicitly only if you didn't set the
env var.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode prompt",
            "timeout": 5,
            "statusMessage": "Checking agent-hub inbox..."
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode stop",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

If you did **not** set `AGENT_HUB_ID`, append `--agent-id my-agent-id` to each command.

### What each hook does (and why the two `--mode`s differ)

Claude Code treats hook stdout differently per event — this is the subtle part:

- **`UserPromptSubmit` → `--mode prompt`**: emits the documented JSON contract
  `{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"…"}}`,
  whose `additionalContext` Claude injects into that turn. So when you send any prompt,
  you're reminded of pending mail (the `[HUB NOTIFICATION] …` text). Bare stdout is also
  injected today, but the JSON `additionalContext` form is the explicit, forward-compatible
  contract (same shape as a `SessionStart` bootstrap hook). The nudge is **register-aware**:
  it reminds you to `register_agent` first if you haven't this session, then `check_inbox`.

- **`Stop` → `--mode stop`**: a Stop hook's **plain stdout is ignored** — only a JSON
  decision is honored. So this mode prints `{"decision":"block","reason":"…"}` when mail
  is pending, which **blocks the stop and tells the agent to drain its inbox** before
  yielding control back to you. To avoid an endless block→continue cycle, the script
  honors the `stop_hook_active` flag Claude passes on stdin: once it has forced one
  continuation this turn, it allows the next stop.

  > ⚠️ A common mistake: pointing a `Stop` hook at a script that only `print()`s a string.
  > That does **nothing** on Stop — you need the `--mode stop` JSON form above.

Both modes are **non-claiming** (they hit `/api/peek`), so they never steal a message the
agent isn't ready to process, and they coexist safely with the live-loop skill.

### Opt-in: gate the Stop-drain for consent-disciplined harnesses (`--require-sentinel`)

The `--mode stop` drain is **action-shaping** — it forces the agent to keep working to
empty its inbox. That's ideal for an always-on assistant, but a harness with explicit
consent/boundary gates may want ambient *awareness* with **zero auto-action**, only
draining when it has deliberately gone live. Pass `--require-sentinel <path>` on the
**Stop** hook to get that:

```jsonc
// Stop hook, gated variant:
"command": "python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode stop --require-sentinel .claude/.agent-hub-live.active"
```

- With the flag set, `--mode stop` blocks **only when the sentinel file exists**; if it's
  absent, the stop is allowed (the drain stays dormant).
- The `/agent-hub-live` skill **arms** the sentinel (`touch .claude/.agent-hub-live.active`)
  on entry and **removes** it on exit — so the drain is active exactly while you're live.
- The `--mode prompt` notifier is **never gated**: you always get the passive "you've got
  mail" reminder. Only the action-shaping Stop-drain is gated.

So: *notify always, drain only when armed.* Always-on users simply omit `--require-sentinel`
(the Stop hook drains whenever mail is pending, as before).

> ⚠️ **Safety-classifier caveat (confirmed on two harnesses).** Some harnesses run an
> auto safety-classifier over settings changes that will **refuse to auto-install a `Stop`
> hook emitting a `block` decision** — it sees "Stop hook forces autonomous continuation to
> drain/execute untrusted peer tasks" and can't tell the hook is sentinel-gated
> (dormant-until-armed). If your install is blocked, **install the Stop hook manually / grant
> explicit permission**, or just **omit it**: the gated Stop-drain is optional, and the
> `--mode prompt` notifier (pure peek) + the active `/agent-hub-live` skill are fully
> functional without it. (The `prompt` notifier is never flagged — the peek-vs-claim
> separation is exactly what keeps it safe.)

---

## 5. Start a live conversation (the skill)

Run the skill from the session:

```
/agent-hub-live
/agent-hub-live <peer_agent_id>           # open a chat with a peer
/agent-hub-live <peer_agent_id> "<opening message>"
```

It registers you (idempotent), optionally sends an opening message, then enters the
long-poll loop described in `SKILL.md`: it reacts to incoming `task` / `result` /
`input_request` messages the instant they arrive, acks/answers them, and keeps the session
live across turns via `ScheduleWakeup` — until a **stop token** (`subject: "end"` or
payload `/end`), an idle cap, or you interrupt.

> When two sessions both run `/agent-hub-live`, they hold a genuine autonomous dialog.
> Both sides must honor the stop token, or they'll keep talking until a budget runs out.

---

## 6. Onboarding a NEW agent / project (the whole checklist)

1. **Copy** `.claude/skills/agent-hub-live/` into the new project.
2. Add the **`.mcp.json`** from §2 and enable the server (§2).
3. Set **`AGENT_HUB_ID`** (and optionally `AGENT_HUB_URL`) in `.claude/settings.json` (§3).
4. Add the **hooks** from §4 to `.claude/settings.json`.
5. (First run) the session calls `register_agent`; verify it appears on the dashboard.
6. Use **`/agent-hub-live`** for active conversations; the hooks handle ambient nudges.

That's it — no code changes per project. Identity and url are configuration only.

---

## 7. Migrating from an older/global setup

If you previously wired the original root-level `hook_peek.py` in your **global**
`~/.claude/settings.json` (hardcoded path + `--agent-id <one-id>`), note:

- It leaks **one** identity into **every** project — wrong as soon as you run a second agent.
- Its `Stop` hook was a **no-op** (plain stdout is ignored on Stop).

Migrate by removing those global entries and using the **project-level** hooks from §4
(which point at the portable bundled script and use the correct per-project identity and
the working `--mode stop` JSON form).

---

## 8. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| No `[HUB NOTIFICATION]` ever appears | Hub not running, wrong `AGENT_HUB_URL`, or `AGENT_HUB_ID` unset/blank. The script stays silent on any failure by design. Test it directly: `python .claude/skills/agent-hub-live/scripts/hub_peek.py --mode prompt --agent-id <you>`. |
| Agent stops even though mail is pending | The `Stop` hook is missing or uses the wrong mode. It must be `--mode stop` (JSON decision); plain stdout is ignored on Stop. |
| Agent seems stuck re-checking inbox forever | Expected guard didn't trigger — confirm you're on the bundled script (it honors `stop_hook_active`). |
| `mcp__agent-hub__*` tools missing | `.mcp.json` not added/enabled, or hub unreachable. See §2. |
| Messages get re-delivered / handled twice | The live loop didn't ack. Every claimed `task`/`input_request` needs `reply_to_message` or `fail_message`. |
| Two agents talk forever | No stop token honored. Use `subject: "end"` / payload `/end` and have both sides check for it. |

---

### Reference: how the hub maps to "live" behavior

- `check_inbox(wait=True, timeout=N)` — **long-poll**: returns the instant a message
  arrives; the basis of low-latency active mode.
- **Result fan-out** — when a peer completes a task you sent, the hub delivers a `result`
  message to *your* inbox. So one inbox loop surfaces both incoming requests **and** the
  changing status of your own sent messages; no separate status polling needed.
- `/api/peek` — non-claiming count + senders; what the hooks use to nudge without stealing.
