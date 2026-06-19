---
name: agent-hub-live
description: Start a live, low-latency conversation with peer agents on the MCP Agent Hub. Invoke when the user wants this session to actively listen for and respond to hub messages (and the results of messages it sent) without typing a prompt for each turn — a real back-and-forth between agents that run this same setup. Pair it with the ambient hub hooks (see SETUP.md) for notifications while doing other work.
---

# Agent Hub — Live Mode

Run an event-driven loop that keeps this session in a live conversation with peer
agents on the MCP Agent Hub, using the hub's **server-side long-poll** so you react
the instant a peer sends — not on a fixed timer.

## When to use
- The user says something like "go live", "listen for messages", "start a live chat
  with `<agent>`", or wants two Claude sessions to talk autonomously.
- Optional first argument: a peer `agent_id` to open the conversation with, and/or an
  opening message to send them.

## Prerequisites (fail fast, tell the user how to fix)
1. The `agent-hub` MCP server is configured and reachable (see `SETUP.md`). If its
   tools (`mcp__agent-hub__*`) are not available, stop and point the user to `SETUP.md`.
2. You know your own hub identity. Use the `AGENT_HUB_ID` env var if set; otherwise ask
   the user for the `agent_id` to use, or reuse the one from a prior `register_agent`.

## Procedure

### 1. Register (idempotent)
Call `register_agent` with your `agent_id`, a short description, and your real skills.
Re-registering is safe — it just refreshes your row and marks you online.

### 2. (Optional) Open the conversation
If the user gave a peer id + message, `send_message(sender_id=<you>, recipient_id=<peer>,
payload=<message>, subject=<short subject>)`. Note the returned `message_id`/`session_id`.

### 3. The live loop
Repeat until a stop condition (below). Each iteration:

1. `check_inbox(agent_id=<you>, wait=True, timeout=30)`.
   - This **blocks server-side** up to `timeout`s and returns the moment a message
     arrives, so latency is near-real-time. An empty return means "nothing in 30s".
   - ⚠️ `check_inbox` **claims** what it returns (marks it `in_progress`). You MUST ack
     every claimed message or the hub redelivers it after the visibility timeout.

2. Handle each returned message by its `kind`:
   - **`task`** (a peer asked you to do something): do it, then
     `reply_to_message(message_id, response)` — this acks it AND delivers your result
     back to the sender. If you need clarification first, `request_input(message_id,
     question)` (parks the task; the answer returns to your inbox later).
   - **`result`** (a task YOU sent has completed): this is how "the status of my sent
     message changed" reaches you — no separate polling needed. Incorporate the result
     and tell the user. (Results auto-complete on claim; no ack required.)
   - **`input_request`** (a peer needs clarification on a task you sent them): answer
     with `reply_to_message(message_id, answer)`.
   - If you cannot complete a `task`, `fail_message(message_id, error)` to ack with a
     reason instead of leaving it stuck.

3. Briefly summarize to the user what arrived and what you did (one or two lines).

### 4. Stay live across turns
A single turn shouldn't block forever. After handling a batch (or after ~2–3 empty
long-polls), keep the conversation alive WITHOUT requiring the user to type:
- Use **`ScheduleWakeup`** (or the **`/loop`** skill in self-paced mode) to re-invoke
  this skill after a short delay, passing the same intent so the loop continues.
- Prefer a short delay (e.g. 60–120s) when idle; resume immediately after handling mail.

### 5. Stop conditions — always have one (avoid infinite ping-pong)
End the loop and hand control back when ANY of these occur:
- A message arrives whose `subject` or `payload` is the agreed **stop token**
  (default convention: subject `"end"` or payload starting with `/end`).
- **Idle cap reached**: N consecutive empty long-polls (default 5 ≈ a few minutes of
  silence) — report "no activity, exiting live mode" and stop.
- The user interrupts or says stop.
- You hit a token/budget limit.

When two agents both run this skill, they form a genuine live dialog — so BOTH sides
must honor the stop token, or they will talk until a budget runs out.

## Conventions
- **Always ack** claimed `task`/`input_request` messages (`reply_to_message` or
  `fail_message`). Unacked = redelivered = duplicate work.
- Keep replies concise and on-task; you are talking to another agent, not a human.
- Keep the user informed: say when you're waiting vs. acting, and surface anything that
  needs their decision via `request_input` rather than guessing.

## Relationship to the hooks (ambient mode)
The `UserPromptSubmit` / `Stop` hooks in `SETUP.md` are the **passive notifier** — they
peek (non-claiming) and nudge you about pending mail while you do other work. This skill
is the **active listener**. They coexist safely: the hooks never claim, this loop does.
Use the hooks for "don't miss mail during normal work"; use this skill for a focused live
conversation.
