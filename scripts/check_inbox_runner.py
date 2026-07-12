"""Peek at an agent's inbox by calling the hub in-process (no running server needed).

This is a local debugging/inspection utility: instead of talking to a *running*
hub over the MCP HTTP protocol, this script imports
mcp_hub.hub directly and calls check_inbox() in the current process. That means it
reads the hub's SQLite database (hub.db) straight off disk, so it works even when
the server is stopped.

Caveat: because it reads hub.db relative to the current working directory, run it
from the project root so it resolves the same database the server uses. If the hub
is running, this still reads the same file (SQLite WAL mode allows concurrent
readers), but it bypasses the server entirely.

Usage:
    # Default agent id (antigravity-cli):
    python scripts/check_inbox_runner.py

    # Any agent id:
    python scripts/check_inbox_runner.py <agent_id>
    python scripts/check_inbox_runner.py claude-code-avdia

Note: check_inbox CLAIMS pending messages (marks them in_progress), same as a real
agent would. It is a peek in intent, but not side-effect free -- claimed messages
become invisible to the real agent until the visibility timeout elapses. Use it on
agents that are not actively working, or be aware of the claim semantics.
"""
import argparse
import asyncio
import os
import sys

# Ensure project root is on sys.path so "import mcp_hub.hub" works when this
# script is run directly (e.g. python scripts/check_inbox_runner.py).
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mcp_hub.hub as hub

DEFAULT_AGENT_ID = "antigravity-cli"


async def main(agent_id: str) -> None:
    print(f"Checking inbox for '{agent_id}'...")
    messages = await hub.check_inbox(agent_id, wait=False)
    if not messages:
        print("Inbox is empty.")
        return

    print(f"Found {len(messages)} message(s) in inbox:")
    for m in messages:
        print("-" * 60)
        print(f"Message ID: {m.get('id')}")
        print(f"Session ID: {m.get('session_id')}")
        print(f"Sender: {m.get('sender_id')}")
        print(f"Kind: {m.get('kind')}")
        print(f"Status: {m.get('status')}")
        print(f"Subject: {m.get('subject')}")
        print(f"Payload: {m.get('payload')}")
        if m.get('response'):
            print(f"Response: {m.get('response')}")
        print("-" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Peek at an agent's inbox in-process (reads hub.db directly).",
    )
    parser.add_argument(
        "agent_id",
        nargs="?",
        default=DEFAULT_AGENT_ID,
        help=f"Agent id whose inbox to check (default: {DEFAULT_AGENT_ID}).",
    )
    args = parser.parse_args()
    asyncio.run(main(args.agent_id))
