import urllib.request
import urllib.parse
import json
import sys
import argparse

def peek_hub(agent_id: str, hub_url: str = "http://127.0.0.1:8000") -> str | None:
    """
    Peeks the Hub for pending messages without claiming them.
    If there are pending messages, returns a nudge string to be injected into the LLM context.
    Returns None if no pending messages or if the Hub is unreachable.
    """
    try:
        url = f"{hub_url}/api/peek?agent_id={urllib.parse.quote(agent_id)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                pending_count = data.get("count", 0)
                if pending_count > 0:
                    senders = data.get("senders", [])
                    senders_str = ", ".join(senders)
                    if pending_count == 1:
                        return f"[HUB NOTIFICATION] You have 1 pending message in your Hub inbox (from {senders_str}). Please use the 'check_inbox' tool to read it and continue your tasks."
                    else:
                        return f"[HUB NOTIFICATION] You have {pending_count} pending messages in your Hub inbox (from {senders_str}). Please use the 'check_inbox' tool to read them and continue your tasks."
    except Exception as e:
        # Silently fail if hub is not reachable; this is just a best-effort nudge
        pass
    
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Peek MCP Hub for pending messages")
    parser.add_argument("--agent-id", type=str, required=True, help="Your Agent ID")
    parser.add_argument("--hub-url", type=str, default="http://127.0.0.1:8000", help="Hub URL")
    
    args = parser.parse_args()
    nudge = peek_hub(args.agent_id, args.hub_url)
    
    if nudge:
        # Print to stdout so the CLI hook system can capture and inject it
        print(nudge)
