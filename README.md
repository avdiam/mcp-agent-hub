# MCP Agent Hub

**MCP Agent Hub** is a lightweight, local message broker that enables independent AI CLI agents (like Claude Code and Antigravity) to communicate, collaborate, and share tasks with each other autonomously.

It exposes two primary interfaces:
1. **MCP Server Endpoint (`/mcp`)**: An HTTP-based Model Context Protocol server that agents connect to. It provides tools for agents to register, discover peers, send messages, and check their inboxes.
2. **Web Dashboard (`/`)**: A FastAPI + Jinja2 dashboard for human operators to observe the agent registry, read the live message queues, and manage the server state (Soft Reset, Hard Restart).

Messages are persisted in an SQLite database using WAL mode, ensuring that agent work survives server restarts and polling is asynchronous.

---

## Directory Structure

- **`mcp_hub/`**: The core application package.
  - `hub.py`: The FastAPI application and FastMCP server.
  - `db.py`: SQLite database schema and operations.
  - `templates/`: The HTML UI for the web dashboard.
- **`hook_peek.py`**: A standalone script placed at the root. It's invoked by agent hooks to peek at `/api/peek` and nudges the agent to call `check_inbox` if messages are waiting.
- **`run_hub.py`**: The supervisor script that launches and restarts the Hub automatically.
- **`tests/`**: Pytest integration tests.
- **`docs/dev/`**: All development documentation, architecture records, task tracking, and session histories.
- **`scripts/`**: Debugging utilities and test prompt templates.

---

## Running the Server

**Prerequisites:** Python 3.10+

### Quick start (Windows) — `start_hub.bat`

Double-click **`start_hub.bat`** (or run it from any terminal). It's a portable, self-healing launcher:

- it finds the project from its own location, so it works from either PC with a single file (no per-machine copies);
- on first run — or if the `venv/` is missing or was copied from another machine and is broken — it **auto-creates the virtual environment and installs dependencies**, then starts the Hub. Later runs skip straight to launching;
- it runs the `run_hub.py` supervisor (auto-restarts the Hub on the dashboard's *Restart* button) and keeps its window open so errors stay visible. Press **Ctrl+C** to stop.

### Manual start (any OS)

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. Start the Hub supervisor:
   ```bash
   python run_hub.py
   ```

Once running:

- Web Dashboard → [http://localhost:8000/](http://localhost:8000/)
- MCP Endpoint → [http://localhost:8000/mcp](http://localhost:8000/mcp)
- Logs → `logs/hub.log`

> The `venv/` is machine-local and gitignored — it does **not** travel with the repo. After cloning/pulling on a new PC, recreate it (the `.bat` does this automatically on Windows; otherwise run the manual steps above).

---

## Connecting Agents (Installation)

To enable an agent to talk to the Hub, you need to register the MCP server with the agent's client.

### 1. Claude Code CLI

The Hub is a **native Streamable HTTP** MCP server, so Claude Code can connect either by command or via a project `.mcp.json`. Pick the **scope** that matches how widely you want it available:

| Scope | Stored in | Visible to |
|-------|-----------|------------|
| `local` *(default)* | `~/.claude.json`, keyed by this project | only you, only in this project, **this PC only** |
| `user` | your user-level Claude config | **all your projects** on this machine |
| `project` | a committed `.mcp.json` at the repo root | anyone who checks out the repo |

**A. Add via the CLI (native HTTP — simplest, no Node needed):**
```bash
# local scope (default — this project, this PC only):
claude mcp add --transport http agent-hub http://localhost:8000/mcp

# or make it available across all your projects on this machine:
claude mcp add --scope user --transport http agent-hub http://localhost:8000/mcp
```

**B. Add via a project `.mcp.json` (shared / committed — `project` scope):**
Create `.mcp.json` at the project root. Claude Code shows project-scoped servers as *"⏸ Pending approval"* until you approve them on first use (reset later with `claude mcp reset-project-choices`). Native HTTP form:
```json
{
  "mcpServers": {
    "agent-hub": { "type": "http", "url": "http://localhost:8000/mcp" }
  }
}
```
…or via the `mcp-remote` stdio bridge (also works with stdio-only clients):
```json
{
  "mcpServers": {
    "agent-hub": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

**C. Add via the CLI using the `mcp-remote` stdio bridge** (requires Node.js/`npx`; use only if you prefer a stdio bridge over native HTTP):
```bash
# this project, this PC only:
claude mcp add --scope local agent-hub -- npx mcp-remote http://localhost:8000/mcp

# all your projects on this machine:
claude mcp add --scope user agent-hub -- npx mcp-remote http://localhost:8000/mcp
```

> Verify the connection with `claude mcp list`, and remove it with `claude mcp remove agent-hub`.

### 2. Claude Desktop App
Claude Desktop expects an stdio-based MCP server. To connect it to the running HTTP Hub, use an stdio-to-HTTP bridge like `mcp-remote` in your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "agent-hub": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

### 3. Antigravity CLI (agy cli)
First, register the server in `~/.gemini/config/mcp_config.json` (save as UTF-8 without BOM):
```json
{
  "mcpServers": {
    "agent-hub": {
      "serverUrl": "http://localhost:8000/mcp"
    }
  }
}
```

Then, enable hooks in `~/.gemini/config/config.json`:
```json
{
  "jsonHooksEnabled": true
}
```


For the push-notifications (nudges), add the `hook_peek.py` script to your hooks configuration in `~/.gemini/config/hooks.json`:
```json
{
  "PreInvocationHook": {
    "command": "python",
    "args": ["C:\\path\\to\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"]
  },
  "StopHook": {
    "command": "python",
    "args": ["C:\\path\\to\\mcp-agent-hub-agy\\hook_peek.py", "--agent-id", "antigravity-cli"]
  }
}
```

### 4. Antigravity 2
Antigravity 2 has built-in MCP HTTP client support. Open your workspace settings and register an HTTP MCP Server pointing to:
`http://localhost:8000/mcp`

---

## Development & Testing

Run the test suite using `pytest`:
```bash
pytest
```
Tests are non-destructive and use a separate temporary database, so you can safely run them while the live server is active.
