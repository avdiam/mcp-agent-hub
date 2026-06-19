"""Portable agent-hub inbox notifier for Claude Code hooks.

Peeks the MCP Agent Hub for *pending* messages WITHOUT claiming them and emits a
nudge so a Claude Code session notices peer traffic between turns. It is the
"ambient" half of the live-messaging setup (the active half is the
/agent-hub-live skill, which runs a long-poll loop).

Designed to be dropped into ANY project unchanged:
  * identity comes from --agent-id OR the AGENT_HUB_ID env var (no hardcoding),
  * hub url comes from --hub-url OR AGENT_HUB_URL (defaults to localhost:8000),
  * stdlib only (urllib) so it needs no dependencies on any machine.

Two modes, because Claude Code treats hook stdout differently per event:

  --mode prompt   (UserPromptSubmit)  -> print the nudge as PLAIN TEXT on stdout.
                  Claude injects plain stdout from a UserPromptSubmit hook into
                  the turn's context, so a bare print is enough.

  --mode stop     (Stop)              -> print JSON {"decision":"block",...}.
                  A Stop hook's plain stdout is IGNORED; only a JSON block
                  decision keeps the agent going. We block once so the agent
                  drains its inbox before yielding, and guard `stop_hook_active`
                  (sent by Claude on the hook's stdin) to avoid infinite loops.

Exit code is always 0: a notifier must never break the user's turn. If the hub
is down or there is no mail, we stay silent and exit 0 (allow normal flow).
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request


def peek(agent_id: str, hub_url: str):
    """Return (count, senders) of pending messages, or (0, []) on any failure."""
    try:
        url = f"{hub_url.rstrip('/')}/api/peek?agent_id={urllib.parse.quote(agent_id)}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=2.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return int(data.get("count", 0)), list(data.get("senders", []))
    except Exception:
        # Best-effort only: a notifier must never raise into the user's session.
        pass
    return 0, []


def nudge_text(count: int, senders: list) -> str:
    who = ", ".join(senders) if senders else "unknown"
    noun = "message" if count == 1 else "messages"
    return (
        f"[HUB NOTIFICATION] You have {count} pending {noun} in your agent-hub "
        f"inbox (from {who}). Use the 'check_inbox' tool to read and handle them "
        f"before stopping."
    )


def read_hook_input() -> dict:
    """Claude passes hook event JSON on stdin. Tolerate absence/garbage."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-hub inbox notifier for Claude Code hooks")
    parser.add_argument("--agent-id", default=os.environ.get("AGENT_HUB_ID"),
                        help="Your hub agent id (default: $AGENT_HUB_ID).")
    parser.add_argument("--hub-url", default=os.environ.get("AGENT_HUB_URL", "http://127.0.0.1:8000"),
                        help="Hub base url (default: $AGENT_HUB_URL or http://127.0.0.1:8000).")
    parser.add_argument("--mode", choices=["prompt", "stop"], default="prompt",
                        help="prompt = plain stdout (UserPromptSubmit); stop = JSON block decision (Stop).")
    args = parser.parse_args()

    hook_input = read_hook_input()

    # Stop-loop guard: if we already forced a continuation this turn, allow the
    # stop so we never trap the agent in an endless block/continue cycle.
    if args.mode == "stop" and hook_input.get("stop_hook_active"):
        return 0

    if not args.agent_id:
        # No identity configured -> stay silent rather than nag with errors.
        return 0

    count, senders = peek(args.agent_id, args.hub_url)
    if count <= 0:
        return 0  # No mail: allow normal flow in both modes.

    message = nudge_text(count, senders)

    if args.mode == "stop":
        # JSON block decision is the ONLY thing a Stop hook honors.
        print(json.dumps({"decision": "block", "reason": message}))
    else:
        # UserPromptSubmit: plain stdout is injected into context.
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
