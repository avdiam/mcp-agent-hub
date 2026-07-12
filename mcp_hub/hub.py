import asyncio
import time
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import Headers, MutableHeaders

from fastmcp import FastMCP, Context
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import BaseModel, Field
from pathlib import Path

from . import db
# Tunables live in db.py (the single source of truth) so tuning them can't silently desync
# the DB logic from the server config — re-exported here for existing `hub.STALE_THRESHOLD`
# style references (AHB-14).
from .db import STALE_THRESHOLD, VISIBILITY_TIMEOUT, MESSAGE_TTL

# Global configurations
DB_PATH = "hub.db"
START_TIME = time.time()
DASHBOARD_MESSAGE_LIMIT = 100
LONGPOLL_INTERVAL = 1.0

# Activity Ring Buffer (D22)
activity_buffer = deque(maxlen=200)

# Dashboard SSE push (D38)
SSE_KEEPALIVE = 20.0   # seconds between comment pings on an idle stream
SSE_DEBOUNCE = 0.25    # coalesce a burst of changes into one push


class StateNotifier:
    """Wakes /api/events streams whenever dashboard-visible state changes (D38).

    A monotonically increasing version + asyncio.Condition. Every mutation site
    (MCP tool calls via ActivityTracker, operator REST endpoints, the sweeper)
    calls bump(); each SSE stream waits for the version to move past what it
    last pushed. Purely in-process, like the activity buffer — a reconnecting
    client just gets a fresh full snapshot.
    """
    def __init__(self):
        self.version = 0
        self._cond = asyncio.Condition()

    async def bump(self):
        async with self._cond:
            self.version += 1
            self._cond.notify_all()

    async def wait_for_change(self, seen: int, timeout: float) -> int:
        """Return the current version, waiting up to `timeout` s for it to move past `seen`."""
        async with self._cond:
            if self.version != seen:
                return self.version
            try:
                await asyncio.wait_for(self._cond.wait_for(lambda: self.version != seen), timeout)
            except asyncio.TimeoutError:
                pass
            return self.version


notifier = StateNotifier()

# Optional background loop for sweeping stale and expired messages
async def background_sweeper():
    while True:
        try:
            reclaimed = await db.reclaim_stale(DB_PATH, visibility_timeout=VISIBILITY_TIMEOUT)
            expired = await db.expire_messages(DB_PATH, message_ttl=MESSAGE_TTL)
            expired_offers = await db.expire_offers(DB_PATH)
            if reclaimed or expired or expired_offers:
                await notifier.bump()
        except Exception as e:
            logging.error(f"Sweeper error: {e}")
        await asyncio.sleep(VISIBILITY_TIMEOUT / 2)

@asynccontextmanager
async def hub_lifespan(app: FastAPI):
    # Startup
    await db.init_db(DB_PATH)
    sweeper_task = asyncio.create_task(background_sweeper())
    yield
    # Shutdown
    sweeper_task.cancel()

from urllib.parse import urlparse

# Origin Validation Middleware (D18) — pure ASGI (D38, closes the AHB-14 item-3 watch-item).
# Deliberately NOT a Starlette BaseHTTPMiddleware: that wrapper can buffer streaming
# responses and delay client-disconnect propagation, and both the /mcp streamable-HTTP
# transport and the /api/events SSE stream flow through here.
class OriginValidationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith("/mcp") or path.startswith("/api/"):
            headers = Headers(scope=scope)
            # 1. Validate Host header to prevent DNS rebinding
            host_name = headers.get("host", "").split(":")[0]
            if host_name not in ("localhost", "127.0.0.1"):
                await JSONResponse({"detail": "Forbidden Host"}, status_code=403)(scope, receive, send)
                return

            # 2. Validate Origin header
            origin = headers.get("origin")
            if origin:
                if urlparse(origin).hostname not in ("localhost", "127.0.0.1"):
                    await JSONResponse({"detail": "Forbidden Origin"}, status_code=403)(scope, receive, send)
                    return
            else:
                # 3. If Origin is missing, use Sec-Fetch-Site to block cross-site requests
                # (Browsers omit Origin on same-origin GET. MCP clients omit both, which is allowed)
                sfs = headers.get("sec-fetch-site")
                if sfs is not None and sfs not in ("same-origin", "none"):
                    await JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)(scope, receive, send)
                    return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                # frame-ancestors only works as an HTTP header (Chrome ignores it in <meta>)
                resp_headers = MutableHeaders(scope=message)
                resp_headers["X-Frame-Options"] = "DENY"
                resp_headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# FastMCP Server Setup
mcp = FastMCP("MCP Agent Hub")

# Pydantic Model for Skills (D16)
class Skill(BaseModel):
    id: str = Field(description="Unique identifier for the skill")
    name: str = Field(description="Human-readable name")
    description: str = Field(description="What the skill does")
    tags: list[str] | None = Field(default=None, description="Keywords for discovery")
    examples: list[str] | None = Field(default=None, description="Example usage prompts")


# FastMCP Middleware (D14, D22, D23)
class ActivityTracker:
    async def __call__(self, context: Context, call_next):
        method = getattr(context, "method", None)
        msg = getattr(context, "message", None)
        tool_name = getattr(msg, "name", "unknown") if (method == "tools/call" and msg) else "unknown"
        args = getattr(msg, "arguments", {}) or {}
        # poster_id is resolve_offer's direct actor arg (AHB-2) — same D23 rule as the others.
        caller = args.get("agent_id") or args.get("sender_id") or args.get("poster_id")

        message_id = args.get("message_id")
        arg_summary = str(args)[:100] + "..." if len(str(args)) > 100 else str(args)

        if caller:
            # Refresh last_seen (D23)
            await db.touch_last_seen(DB_PATH, caller)

        # Activity-feed display attribution only — NOT last_seen (D23 deliberately excludes
        # these). reply/fail/request_input/check_status carry only a message_id, so resolve who
        # acted from the message row instead of logging them as "System" (AHB-14): check_status
        # is the sender polling its own message; the ack tools are the recipient acting on a
        # message it claimed from its inbox.
        display_agent = caller
        if not display_agent and message_id:
            endpoints = await db.get_message_endpoints(DB_PATH, message_id)
            if endpoints:
                display_agent = endpoints["sender_id"] if tool_name == "check_status" else endpoints["recipient_id"]

        try:
            result = await call_next(context)
            outcome = "success"
            error_str = None
        except Exception as e:
            import traceback
            outcome = "error"
            error_str = str(e) + "\n" + traceback.format_exc()
            activity_buffer.append({
                "timestamp": time.time(),
                "agent": display_agent,
                "tool": tool_name,
                "outcome": outcome,
                "message_id": message_id,
                "args": arg_summary,
                "error": error_str
            })
            await notifier.bump()  # D38: even a failed call changes the activity feed
            raise e

        activity_buffer.append({
            "timestamp": time.time(),
            "agent": display_agent,
            "tool": tool_name,
            "outcome": outcome,
            "message_id": message_id,
            "args": arg_summary,
            "error": error_str
        })
        # D38: every tool call changes dashboard state (at minimum the activity feed).
        await notifier.bump()
        return result

mcp.add_middleware(ActivityTracker())

# --- MCP Tools ---

@mcp.tool()
async def register_agent(agent_id: str, skills: list[Skill] | None = None, description: str | None = None) -> str:
    """
    Register yourself with the Hub so others can discover and send messages to you.
    Provide an accurate list of your skills so peers know what tasks to route to you.
    Omitting skills (or description) on a re-register keeps what you advertised before,
    so a bare register_agent(agent_id) is a safe liveness refresh.
    You MUST call this before sending or checking messages.
    Registering also queues any broadcast announcements from the last 24h that you missed
    (e.g. sent while you were offline or before you joined) into your inbox — read them via
    check_inbox; they are ack-less like any announcement.
    """
    # AHB-18: skills is optional — a required list drove first-time CLI agents to skip
    # registration entirely. None means "keep existing" (or [] for a brand-new agent).
    skills_json = json.dumps([s.model_dump() for s in skills]) if skills is not None else None
    await db.upsert_agent(DB_PATH, agent_id, skills_json, description)
    # AHB-1 P2: catch the registrant up on broadcasts it missed (late joiner / was offline).
    missed = await db.deliver_missed_broadcasts(DB_PATH, agent_id)
    result = f"Successfully registered agent {agent_id}"
    if missed:
        result += f" — {missed} missed announcement(s) queued in your inbox; check_inbox to read them"
    return result

@mcp.tool()
async def list_agents() -> list[dict]:
    """
    List all currently registered agents, their status, and their advertised skills.
    Use this to find an appropriate peer agent to send your task to.
    Status reflects actual liveness: 'online' (recently active), 'stale' (registered but
    silent past the freshness threshold — may be idle or gone), or 'offline' (explicitly
    disconnected). Prefer 'online' agents when routing time-sensitive tasks.
    """
    return await db.get_all_agents(DB_PATH)

@mcp.tool()
async def send_message(sender_id: str, recipient_id: str, payload: str, context: str | None = None, session_id: str | None = None, subject: str | None = None) -> dict:
    """
    Send a task or message to another agent.
    If continuing an existing conversation, provide the session_id.
    Returns the message_id which you can use to check status later via check_status.
    Your result will also be delivered to your inbox as a 'result' message when completed.
    """
    return await db.enqueue_message(DB_PATH, sender_id, recipient_id, payload, context, session_id, subject=subject)

@mcp.tool()
async def broadcast_message(sender_id: str, payload: str, subject: str | None = None, context: str | None = None) -> dict:
    """
    Broadcast one announcement to ALL currently connected agents at once (yourself included).
    Use this for something every peer should see — a status update, an offer, a heads-up — NOT
    for addressing one agent (use send_message for that). Announcements are informational and
    ack-less: recipients read them via check_inbox and must NOT reply_to_message / fail_message.
    Flood-capped per sender (a short cooldown, an hourly limit, and payload/subject size limits);
    a violation delivers nothing and returns {"ok": false, "error": ...}. On success returns
    {"ok": true, "broadcast_id", "delivered", "recipients", "skipped_offline", ...}.
    """
    try:
        result = await db.broadcast(DB_PATH, sender_id, payload, subject=subject, context=context)
        return {"ok": True, **result}
    except ValueError as e:
        return {"ok": False, "error": str(e), "delivered": 0}

@mcp.tool()
async def check_inbox(agent_id: str, wait: bool = True, timeout: int = 30) -> list[dict]:
    """
    Check your inbox for new tasks, clarification requests, task results, or task failures.
    By default (wait=True), this blocks and waits up to `timeout` seconds for a message to arrive.
    It is highly recommended to call this in a loop to wait for work.
    Returns a list of messages. Claimed messages MUST be acknowledged with reply_to_message or fail_message!
    """
    start = time.time()
    while True:
        claimed = await db.claim_pending(DB_PATH, agent_id, visibility_timeout=VISIBILITY_TIMEOUT)
        if claimed:
            return claimed
        if not wait or (time.time() - start >= timeout):
            return []
        await asyncio.sleep(LONGPOLL_INTERVAL)

@mcp.tool()
async def reply_to_message(message_id: str, response: str) -> str:
    """
    Mark a claimed message as successfully completed and provide the response.
    If the message was a task, the original sender will receive the response.
    If the message was a clarification question (input_request), answering it will unpark the original task.
    """
    await db.complete_message(DB_PATH, message_id, response)
    return f"Message {message_id} completed."

@mcp.tool()
async def fail_message(message_id: str, error: str) -> str:
    """
    Mark a claimed message as failed with an error description.
    If the message was a task, the original sender is notified via a 'failure' message in
    their inbox (the mirror of the 'result' they'd get on success — no status polling needed).
    If the message was a clarification question (input_request) you couldn't answer, the
    original task is handed back to its worker (as pending) with your failure noted, rather
    than left parked forever.
    """
    await db.fail_message(DB_PATH, message_id, error)
    return f"Message {message_id} failed."

@mcp.tool()
async def request_input(message_id: str, question: str) -> dict:
    """
    If you are handling a task and need clarification from the sender, call this.
    It parks the current task and asks the sender your question.
    Once the sender replies, the original task will reappear in your inbox with the answer attached to the context.
    """
    return await db.request_input(DB_PATH, message_id, question)

@mcp.tool()
async def check_status(message_id: str) -> dict:
    """
    Check the current status of a message you sent.
    Note: You will also receive the result in your inbox when completed, so polling this is usually not necessary.
    """
    return await db.get_status(DB_PATH, message_id)

@mcp.tool()
async def post_offer(sender_id: str, payload: str, subject: str | None = None,
                     required_skills: list[str] | None = None, ttl_seconds: int | None = None) -> dict:
    """
    Post a job offer to the hub's job board: work open for ANY agent to claim, not addressed
    to a specific recipient (use send_message for that). The offer is announced to all
    connected agents as a broadcast — your broadcast flood caps apply — and stays on the
    board until assigned, withdrawn, or expired (default TTL 24h). Interested agents call
    claim_offer; claims accumulate and YOU pick exactly one winner with
    resolve_offer(action='select') whenever you're ready, or take it down with
    resolve_offer(action='withdraw'). On selection the hub automatically sends your payload
    to the winner as a normal task, so you'll get its result/failure in your inbox like any
    task you sent; a completed assignment marks the offer 'completed' on the board.
    IMPORTANT: make `payload` the pure work statement only — it is delivered VERBATIM as the
    winner's task. Do not include how-to-claim instructions; the hub appends those to the
    advert automatically. Returns {"ok": true, "offer_id", ...} or {"ok": false, "error"} on
    a cap violation (payload/subject size, 5-open-offers-per-poster, broadcast cooldown).
    """
    try:
        result = await db.post_offer(
            DB_PATH, sender_id, payload, subject=subject, required_skills=required_skills,
            ttl=ttl_seconds if ttl_seconds else db.OFFER_DEFAULT_TTL,
        )
        return {"ok": True, **result}
    except ValueError as e:
        return {"ok": False, "error": str(e)}

@mcp.tool()
async def claim_offer(agent_id: str, offer_id: str, note: str | None = None) -> dict:
    """
    Claim an open job offer from the board: express that you want to do the work. Claiming
    does NOT assign it to you — the poster reviews all claims and selects one claimant; if
    that's you, the work arrives in your inbox as a normal task (reply_to_message /
    fail_message it like any task). If you're not selected, or the offer is withdrawn or
    expires, you'll get an informational (ack-less) offer_update message instead — so after
    claiming, just keep checking your inbox as usual: EVERY outcome is pushed to you (by the
    offer's expires_at at the latest); never poll the board for your claim's fate. Add a
    short `note` to make your case (relevant skills, availability). One pending claim per
    offer; you may claim again if the offer re-opens.
    Returns {"ok": true, "claim_id", "pending_claims", "expires_at", "next"} or
    {"ok": false, "error"}.
    """
    try:
        result = await db.claim_offer(DB_PATH, agent_id, offer_id, note=note)
        return {"ok": True, **result}
    except ValueError as e:
        return {"ok": False, "error": str(e)}

@mcp.tool()
async def resolve_offer(poster_id: str, offer_id: str, action: str, claimant_id: str | None = None) -> dict:
    """
    Decide the outcome of a job offer YOU posted.
    action='select' (requires claimant_id): assign the offer to that pending claimant. The
    hub sends your offer payload to them as a normal task (its result/failure comes back to
    your inbox), and every other claimant is notified they weren't selected.
    action='withdraw': take the offer off the board; pending claimants are notified.
    If the selected claimant later fails the task, the offer automatically re-opens on the
    board (within its TTL) so others can claim it.
    Returns {"ok": true, "status", "task_message_id", ...} or {"ok": false, "error"}.
    """
    try:
        result = await db.resolve_offer(DB_PATH, poster_id, offer_id, action, claimant_id=claimant_id)
        return {"ok": True, **result}
    except ValueError as e:
        return {"ok": False, "error": str(e)}

@mcp.tool()
async def list_offers(status: str | None = "open") -> list[dict]:
    """
    Browse the hub's job board. By default returns OPEN (claimable) offers, newest first,
    each with its full payload, required skills, and current claims. Pass
    status='assigned'/'completed'/'withdrawn'/'expired' for history, or status='all' for
    everything. Use this to find work matching your skills, then claim_offer what you want
    to do.
    """
    if status in (None, "", "all"):
        status = None
    return await db.list_offers(DB_PATH, status=status)

@mcp.tool()
async def disconnect_agent(agent_id: str) -> str:
    """
    Explicitly mark yourself as offline. Peers will not be able to send new messages to you.
    """
    await db.set_agent_offline(DB_PATH, agent_id)
    return f"Agent {agent_id} disconnected."


# Assemble the FastAPI application
mcp_app = mcp.http_app(path="/", transport="streamable-http")

app = FastAPI(lifespan=combine_lifespans(hub_lifespan, mcp_app.lifespan))

# Add Origin validation middleware
app.add_middleware(OriginValidationMiddleware)

# Mount the MCP ASGI app at /mcp
app.mount("/mcp", mcp_app)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
               '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
               '<stop offset="0%" stop-color="#6366F1"/>'
               '<stop offset="100%" stop-color="#EC4899"/>'
               '</linearGradient></defs>'
               '<rect width="32" height="32" rx="8" fill="url(#g)"/>'
               '<path d="M17 3L7 18h6v11l10-15h-6z" fill="white"/></svg>')

@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return Response(status_code=204)

async def build_state():
    """Assemble the full dashboard snapshot — served by /api/state (pull) and pushed
    per change over /api/events (D38)."""
    # get_all_agents returns status already liveness-derived (AHB-15/D34)
    agents = await db.get_all_agents(DB_PATH)

    now = time.time()
    for a in agents:
        # Parse skills back to list for JSON response
        try:
            a["skills"] = json.loads(a["skills"]) if a["skills"] else []
        except:
            a["skills"] = []

    messages = await db.get_recent_messages(DB_PATH, limit=DASHBOARD_MESSAGE_LIMIT)
    stats = await db.get_stats(DB_PATH)
    offers = await db.list_offers(DB_PATH)  # all statuses — the board panel filters client-side

    uptime = now - START_TIME
    stats["uptime"] = uptime

    return {
        "agents": agents,
        "messages": messages,
        "offers": offers,
        "events": list(activity_buffer),
        "stats": stats
    }

@app.get("/api/state")
async def api_state():
    return await build_state()

@app.get("/api/events")
async def api_events(request: Request):
    # Dashboard live stream (D38): one SSE `data:` event = one full state snapshot,
    # pushed on connect and then whenever the StateNotifier version moves (debounced
    # so a burst of tool calls coalesces into one push). Comment-line keepalives mark
    # idle streams alive; EventSource reconnects transparently and every (re)connect
    # starts with a fresh snapshot, so the stream is stateless.
    async def stream():
        seen = notifier.version - 1  # force an immediate first snapshot
        while True:
            if await request.is_disconnected():
                break
            current = await notifier.wait_for_change(seen, timeout=SSE_KEEPALIVE)
            if current == seen:
                yield ": keepalive\n\n"
                continue
            await asyncio.sleep(SSE_DEBOUNCE)
            seen = notifier.version  # re-read after the debounce: swallow the burst
            state = await build_state()
            yield f"data: {json.dumps(state)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/peek")
async def api_peek(agent_id: str):
    # AHB-3: peeking your own inbox is a presence signal — refresh last_seen so an
    # ambient-hook session that peeks every turn doesn't decay to stale between turns.
    # No-op for an unknown agent_id (UPDATE matches no row). Read-only /api/state stays
    # untouched: it carries no single actor and must not warm every agent at once.
    await db.touch_last_seen(DB_PATH, agent_id)
    peek_res = await db.peek_inbox(DB_PATH, agent_id)
    await notifier.bump()  # last_seen moved → agent liveness display may change
    return peek_res

@app.post("/api/reset")
async def api_reset():
    cleared = len(activity_buffer)
    activity_buffer.clear()
    reclaimed = await db.reset_stuck(DB_PATH)
    await notifier.bump()
    return {"ok": True, "cleared_events": cleared, "reclaimed_messages": reclaimed}

@app.post("/api/restart")
async def api_restart():
    # Delay restart so response can flush
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, lambda: os._exit(42))
    return {"ok": True, "restarting": True}

@app.post("/api/agents/{agent_id}/disconnect")
async def api_agents_disconnect(agent_id: str):
    await db.set_agent_offline(DB_PATH, agent_id)
    await notifier.bump()
    return {"ok": True, "agent_id": agent_id, "status": "offline"}

@app.post("/api/agents/{agent_id}/delete")
async def api_agents_delete(agent_id: str, purge_messages: bool = False):
    # Option A (default): remove the agent row only, keep its message history.
    # Option B (purge_messages=True): also delete the agent's messages.
    result = await db.delete_agent(DB_PATH, agent_id, purge_messages=purge_messages)
    if result["agents_deleted"] == 0:
        return JSONResponse({"ok": False, "detail": "Agent not found"}, status_code=404)
    await notifier.bump()
    return {"ok": True, "agent_id": agent_id, **result}

@app.post("/api/purge")
async def api_purge():
    purged = await db.delete_old(DB_PATH)
    if purged:
        await notifier.bump()
    return {"ok": True, "deleted": purged}

class BroadcastBody(BaseModel):
    payload: str = Field(min_length=1)
    subject: str | None = None

@app.post("/api/broadcast")
async def api_broadcast(body: BroadcastBody):
    # Operator announcement from the dashboard (AHB-1 P2). Same db.broadcast path and flood
    # caps as the MCP tool; the fixed "operator" sender is rate-limited like any agent but
    # isn't a registered agent, so it gets no self-echo row. Late joiners still receive it
    # via the register-time catch-up (the broadcasts audit row is the durable source).
    try:
        result = await db.broadcast(DB_PATH, "operator", body.payload, subject=body.subject)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    await notifier.bump()
    return {"ok": True, **result}
