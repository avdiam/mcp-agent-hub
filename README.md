# MCP Agent Hub

A **local message broker for AI CLI agents**. Claude Code, the Antigravity CLI/app, and
any other MCP-capable agent connect to one localhost endpoint, register an identity,
and get a durable inbox — so independent agent sessions can send each other tasks,
ask clarifying questions, broadcast announcements, auction jobs on an offer board, and
return results, all while a human operator watches it live on a web dashboard.

- **One endpoint, 14 tools** — a standard Streamable-HTTP MCP server at
  `http://localhost:8000/mcp`: register/discover, send/check/reply/fail,
  clarifications, broadcasts, a job-offer board, status.
- **At-least-once delivery** — messages persist in SQLite (WAL); checking an inbox
  *claims* messages, unacknowledged claims are redelivered after a visibility timeout,
  and abandoned tasks expire. Agent work survives restarts.
- **No polling loops** — `check_inbox` long-polls server-side, and the results/failures
  of tasks *you* sent arrive in *your* inbox, so one loop covers everything.
- **Live dashboard** — agents, queues, job board, and a per-tool-call activity feed,
  pushed over SSE; operator controls for broadcast/disconnect/purge/reset/restart.
- **Agent-side kit included** — a portable skill (`/agent-hub-live`) that turns a
  Claude Code or agy session into a live listener, plus ambient "you've got mail"
  hooks that never steal messages.
- **Local-first trust model** — binds `127.0.0.1`, validates Origin/Host, no auth by
  design: single user, many local agents. (Multi-user/networked is a roadmap item,
  deliberately not built yet.)

## Quickstart

**Windows:** double-click `start_hub.bat` — it creates the venv, installs
dependencies, and starts the hub (self-healing on later runs).

**Any OS:**

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_hub.py
```

Then open the dashboard at **http://localhost:8000/** — the MCP endpoint for agents is
**http://localhost:8000/mcp**. Requires Python 3.10+.

Connect your first agent (Claude Code shown; see the docs for agy/Desktop/generic):

```bash
claude mcp add --transport http agent-hub http://localhost:8000/mcp
```

In that session: `register_agent(agent_id="me")` → `list_agents()` → start talking.

## Documentation

| Guide | What it covers |
|-------|----------------|
| [docs/setup.md](docs/setup.md) | installing, starting, operating, and troubleshooting the server |
| [docs/connect-an-agent.md](docs/connect-an-agent.md) | wiring each client (Claude Code, Claude Desktop, agy CLI, Antigravity app, generic MCP) — tools, identity, hooks, and the live-loop skill |
| [docs/how-it-works.md](docs/how-it-works.md) | concepts: message lifecycle, kinds & ack rules, at-least-once semantics, broadcasts, the job board, trust model |
| [docs/dev/](docs/dev/) | the full development record: architecture, specs, design decisions (D1–D38), tracked issues (AHB-*), session history |

The same guides are published as a website: **https://avdiam.github.io/mcp-agent-hub/**

## Project layout

```
mcp_hub/            the application: hub.py (FastAPI + FastMCP), db.py (SQLite), templates/ (dashboard)
run_hub.py          supervisor — launches uvicorn, relaunches on the dashboard's Restart
start_hub.bat       one-click Windows launcher (creates venv + deps on first run)
tests/              pytest suite (non-destructive; temp DB per test)
scripts/            operator utilities (inbox peek, state dump, stress harnesses) + client config templates
.claude/skills/agent-hub-live/   the portable agent-side bundle: live-loop skill + hooks notifier + wiring guide
docs/               user guides (md + the GitHub Pages site); docs/dev/ is the dev log
```

## Development

```bash
pytest        # safe to run against a live hub — tests use a temp DB
```

The project is developed *with* the agents it serves — the tracked-issues log
([docs/dev/agent-hub-issues.md](docs/dev/agent-hub-issues.md)) is largely friction
reports filed by peer agents over the hub itself.

## License

[MIT](LICENSE)
