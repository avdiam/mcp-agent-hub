import asyncio
import time
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

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

# Optional background loop for sweeping stale and expired messages
async def background_sweeper():
    while True:
        try:
            await db.reclaim_stale(DB_PATH, visibility_timeout=VISIBILITY_TIMEOUT)
            await db.expire_messages(DB_PATH, message_ttl=MESSAGE_TTL)
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

# Origin Validation Middleware (D18)
# Watch-item (AHB-14): this is a Starlette BaseHTTPMiddleware, which in some versions buffers
# streaming responses; the MCP transport is streamable-HTTP. No issue observed in practice —
# if streaming ever hiccups under load, rewrite this as pure ASGI middleware.
class OriginValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/mcp") or request.url.path.startswith("/api/"):
            # 1. Validate Host header to prevent DNS rebinding
            host = request.headers.get("host", "")
            host_name = host.split(":")[0]
            if host_name not in ("localhost", "127.0.0.1"):
                return JSONResponse({"detail": "Forbidden Host"}, status_code=403)

            # 2. Validate Origin header
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.hostname not in ("localhost", "127.0.0.1"):
                    return JSONResponse({"detail": "Forbidden Origin"}, status_code=403)
            else:
                # 3. If Origin is missing, use Sec-Fetch-Site to block cross-site requests
                # (Browsers omit Origin on same-origin GET. MCP clients omit both, which is allowed)
                sfs = request.headers.get("sec-fetch-site")
                if sfs is not None and sfs not in ("same-origin", "none"):
                    return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
                    
        response = await call_next(request)
        # frame-ancestors only works as an HTTP header (Chrome ignores it in <meta>)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response


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
        caller = args.get("agent_id") or args.get("sender_id")

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
        return result

mcp.add_middleware(ActivityTracker())

# --- MCP Tools ---

@mcp.tool()
async def register_agent(agent_id: str, skills: list[Skill], description: str | None = None) -> str:
    """
    Register yourself with the Hub so others can discover and send messages to you.
    Provide an accurate list of your skills so peers know what tasks to route to you.
    You MUST call this before sending or checking messages.
    """
    skills_dict = [s.model_dump() for s in skills]
    skills_json = json.dumps(skills_dict)
    await db.upsert_agent(DB_PATH, agent_id, skills_json, description)
    return f"Successfully registered agent {agent_id}"

@mcp.tool()
async def list_agents() -> list[dict]:
    """
    List all currently registered agents, their status, and their advertised skills.
    Use this to find an appropriate peer agent to send your task to.
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

@app.get("/api/state")
async def api_state():
    agents = await db.get_all_agents(DB_PATH)
    
    # Process agents to add derived status
    now = time.time()
    for a in agents:
        if a["status"] != "offline":
            if now - a["last_seen"] > STALE_THRESHOLD:
                a["status"] = "stale"
            else:
                a["status"] = "online"
        
        # Parse skills back to list for JSON response
        try:
            a["skills"] = json.loads(a["skills"]) if a["skills"] else []
        except:
            a["skills"] = []
            
    messages = await db.get_recent_messages(DB_PATH, limit=DASHBOARD_MESSAGE_LIMIT)
    stats = await db.get_stats(DB_PATH)
    
    uptime = now - START_TIME
    stats["uptime"] = uptime
    
    return {
        "agents": agents,
        "messages": messages,
        "events": list(activity_buffer),
        "stats": stats
    }

@app.get("/api/peek")
async def api_peek(agent_id: str):
    # AHB-3: peeking your own inbox is a presence signal — refresh last_seen so an
    # ambient-hook session that peeks every turn doesn't decay to stale between turns.
    # No-op for an unknown agent_id (UPDATE matches no row). Read-only /api/state stays
    # untouched: it carries no single actor and must not warm every agent at once.
    await db.touch_last_seen(DB_PATH, agent_id)
    peek_res = await db.peek_inbox(DB_PATH, agent_id)
    return peek_res

@app.post("/api/reset")
async def api_reset():
    cleared = len(activity_buffer)
    activity_buffer.clear()
    reclaimed = await db.reset_stuck(DB_PATH)
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
    return {"ok": True, "agent_id": agent_id, "status": "offline"}

@app.post("/api/agents/{agent_id}/delete")
async def api_agents_delete(agent_id: str, purge_messages: bool = False):
    # Option A (default): remove the agent row only, keep its message history.
    # Option B (purge_messages=True): also delete the agent's messages.
    result = await db.delete_agent(DB_PATH, agent_id, purge_messages=purge_messages)
    if result["agents_deleted"] == 0:
        return JSONResponse({"ok": False, "detail": "Agent not found"}, status_code=404)
    return {"ok": True, "agent_id": agent_id, **result}

@app.post("/api/purge")
async def api_purge():
    purged = await db.delete_old(DB_PATH)
    return {"ok": True, "deleted": purged}
