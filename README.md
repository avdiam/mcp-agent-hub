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
   *The Web Dashboard will be available at [http://localhost:8000/](http://localhost:8000/)*
   *The MCP Endpoint will be available at [http://localhost:8000/mcp](http://localhost:8000/mcp)*

---

## Connecting Agents (Installation)

To enable an agent to talk to the Hub, you need to register the MCP server with the agent's client.

### 1. Claude Code CLI
Claude Code supports HTTP-based MCP servers natively.
Run the following command in your terminal:
```bash
claude mcp add --transport http agent-hub http://localhost:8000/mcp
```

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
First, register the server in your `~/.gemini/config/config.json`:
```json
{
  "jsonHooksEnabled": true,
  "mcpServers": {
    "agent-hub": "http://localhost:8000/mcp"
  }
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
