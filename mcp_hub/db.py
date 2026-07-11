import asyncio
from functools import wraps
from contextlib import asynccontextmanager
import sqlite3
import aiosqlite
import json
import time
import uuid

if sqlite3.sqlite_version_info < (3, 35):
    raise RuntimeError(f"SQLite >= 3.35 is required. Found: {sqlite3.sqlite_version}")

# Kinds that auto-complete the instant they are claimed (ack-less): the recipient never
# reply/fail's them, so claim_pending marks them 'completed' on the way out.
#   result       = a task YOU sent succeeded (D20)
#   failure      = a task YOU sent failed    (AHB-13 #3)
#   announcement = a broadcast delivered to everyone (AHB-1 BD2 — informational, no reply)
NO_ACK_KINDS = ("result", "failure", "announcement")

# Tunable queue constants — the single source of truth. hub.py imports these (rather than
# redefining them) so tuning one here can't silently desync the DB logic from the server
# config; each is also overridable per-call via the matching keyword arg. (Q5; AHB-14)
STALE_THRESHOLD = 90       # seconds since last_seen before a queued recipient is flagged stale
VISIBILITY_TIMEOUT = 600   # seconds an in_progress claim is held before it's eligible for redelivery
MESSAGE_TTL = 86400        # seconds a pending task lingers before the sweep marks it expired

# Broadcast flood caps (AHB-1 P1). A violation raises ValueError and inserts NOTHING
# (all-or-nothing). Enforced inside db.broadcast so the DB stays the single source of truth
# (per the D32/AHB-14 precedent); each is overridable per-call via the matching keyword arg.
BROADCAST_MAX_PAYLOAD = 4096    # bytes (UTF-8) — max broadcast body
BROADCAST_MAX_SUBJECT = 120     # characters — max broadcast subject
BROADCAST_MIN_INTERVAL = 30     # seconds — per-sender cooldown between broadcasts
BROADCAST_HOURLY_CAP = 10       # broadcasts per sender per rolling hour
BROADCAST_MAX_RECIPIENTS = 200  # safety ceiling on fan-out size per broadcast



def retry_on_lock(retries=5, backoff=0.01):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e) and attempt < retries - 1:
                        await asyncio.sleep(backoff * (2 ** attempt))
                    else:
                        raise
        return wrapper
    return decorator

@asynccontextmanager
async def _connect(db_path):
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()

@retry_on_lock()
async def init_db(db_path="hub.db"):
    async with _connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=5000")
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                description TEXT,
                skills TEXT,
                status TEXT,
                last_seen REAL
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                parent_id TEXT,
                kind TEXT DEFAULT 'task',
                sender_id TEXT,
                recipient_id TEXT,
                payload TEXT,
                context TEXT,
                response TEXT,
                status TEXT DEFAULT 'pending',
                flagged_stale INTEGER DEFAULT 0,
                claimed_at REAL,
                created_at REAL,
                updated_at REAL,
                subject TEXT
            )
        """)
        

        try:
            await db.execute("ALTER TABLE messages ADD COLUMN subject TEXT")
        except sqlite3.OperationalError:
            pass
        
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_recipient_status ON messages(recipient_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_session ON messages(session_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_created_at ON messages(created_at)")

        # Broadcast audit log (AHB-1 P1). Doubles as the durable rate-limit source (survives
        # restarts, unlike an in-memory bucket): db.broadcast reads it to enforce the caps.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id TEXT PRIMARY KEY,
                sender_id TEXT,
                subject TEXT,
                payload TEXT,
                recipient_count INTEGER,
                created_at REAL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcasts_sender_created ON broadcasts(sender_id, created_at)")

        await db.commit()

@retry_on_lock()
async def upsert_agent(db_path, agent_id, skills_json, description=None):
    async with _connect(db_path) as db:
        await db.execute("""
            INSERT INTO agents (id, description, skills, status, last_seen)
            VALUES (?, ?, ?, 'online', ?)
            ON CONFLICT(id) DO UPDATE SET
                description=excluded.description,
                skills=excluded.skills,
                status='online',
                last_seen=excluded.last_seen
        """, (agent_id, description, skills_json, time.time()))
        await db.commit()

def derive_status(stored_status, last_seen, now=None, stale_threshold=STALE_THRESHOLD):
    """Liveness as surfaced to consumers: the stored `status` column is only the sticky
    online/offline intent flag; actual presence is derived from `last_seen` age (AHB-15).
    Explicit `offline` is preserved; anything else reads `online` or `stale` by age."""
    if stored_status == "offline":
        return "offline"
    if now is None:
        now = time.time()
    if last_seen is None or now - last_seen > stale_threshold:
        return "stale"
    return "online"

@retry_on_lock()
async def get_all_agents(db_path):
    """Registry rows with `status` already liveness-derived (AHB-15) — every consumer
    (MCP `list_agents`, `/api/state`) shares this one derivation so they can't diverge."""
    now = time.time()
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM agents") as cursor:
            rows = await cursor.fetchall()
            agents = [dict(row) for row in rows]
    for a in agents:
        a["status"] = derive_status(a["status"], a["last_seen"], now)
    return agents

@retry_on_lock()
async def set_agent_offline(db_path, agent_id):
    async with _connect(db_path) as db:
        await db.execute("UPDATE agents SET status='offline' WHERE id=?", (agent_id,))
        await db.commit()

@retry_on_lock()
async def delete_agent(db_path, agent_id, purge_messages=False):
    """Permanently remove an agent from the registry.

    Option A (default): deletes the agent row only; its historical messages are
    left intact. Option B (purge_messages=True): also deletes every message the
    agent sent or received. Returns a dict with the rows removed from each table.
    """
    async with _connect(db_path) as db:
        messages_deleted = 0
        if purge_messages:
            cursor = await db.execute(
                "DELETE FROM messages WHERE sender_id=? OR recipient_id=?",
                (agent_id, agent_id),
            )
            messages_deleted = cursor.rowcount
        cursor = await db.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        agents_deleted = cursor.rowcount
        await db.commit()
        return {"agents_deleted": agents_deleted, "messages_deleted": messages_deleted}

@retry_on_lock()
async def touch_last_seen(db_path, agent_id):
    async with _connect(db_path) as db:
        await db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent_id))
        await db.commit()

@retry_on_lock()
async def enqueue_message(db_path, sender_id, recipient_id, payload, context=None, session_id=None, parent_id=None, kind="task", response=None, subject=None, internal=False, stale_threshold=STALE_THRESHOLD):
    message_id = str(uuid.uuid4())
    if not session_id:
        session_id = str(uuid.uuid4())

    async with _connect(db_path) as db:
        async with db.execute("SELECT status, last_seen FROM agents WHERE id=?", (recipient_id,)) as cursor:
            row = await cursor.fetchone()
            # Point-to-point sends reject unknown/offline recipients. Internal deliveries
            # (result / input_request fan-out) MUST bypass that guard: the originating task
            # already completed, so raising here would surface a spurious error to the
            # worker AND drop the fan-out. Mirrors the AHB-1 BD3 broadcast rule. (AHB-11)
            if not row:
                if not internal:
                    raise ValueError(f"Recipient {recipient_id} is unknown")
                is_stale = 0
            else:
                if row["status"] == "offline" and not internal:
                    raise ValueError(f"Recipient {recipient_id} is offline")
                is_stale = 0
                if time.time() - row["last_seen"] > stale_threshold:
                    is_stale = 1

        now = time.time()
        await db.execute("""
            INSERT INTO messages (
                id, session_id, parent_id, kind, sender_id, recipient_id,
                payload, context, response, status, flagged_stale, created_at, updated_at, subject
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """, (message_id, session_id, parent_id, kind, sender_id, recipient_id, payload, context, response, is_stale, now, now, subject))
        await db.commit()
    
    return {"message_id": message_id, "session_id": session_id}

@retry_on_lock()
async def broadcast(db_path, sender_id, payload, subject=None, context=None,
                    max_payload=BROADCAST_MAX_PAYLOAD, max_subject=BROADCAST_MAX_SUBJECT,
                    min_interval=BROADCAST_MIN_INTERVAL, hourly_cap=BROADCAST_HOURLY_CAP,
                    max_recipients=BROADCAST_MAX_RECIPIENTS, stale_threshold=STALE_THRESHOLD):
    """Fan one announcement out to every connected agent (AHB-1 P1).

    Delivers a kind='announcement' message (ack-less; auto-completed on claim, NO_ACK_KINDS)
    to all non-offline agents INCLUDING the sender (BD5 echo), skipping explicitly-offline
    peers (BD3). Flood-capped per sender via the durable `broadcasts` audit table; a cap
    violation raises ValueError and inserts NOTHING (all-or-nothing). Returns delivery counts
    and the broadcast_id. One multi-row transaction under @retry_on_lock.
    """
    # Size caps first — cheap, and reject before opening a connection.
    if payload is not None and len(payload.encode("utf-8")) > max_payload:
        raise ValueError(f"Broadcast payload exceeds the {max_payload}-byte limit")
    if subject is not None and len(subject) > max_subject:
        raise ValueError(f"Broadcast subject exceeds the {max_subject}-character limit")

    broadcast_id = str(uuid.uuid4())
    now = time.time()

    async with _connect(db_path) as db:
        # Rate limits, read from the durable audit log (survives restarts).
        async with db.execute(
            "SELECT COUNT(*) AS n, MAX(created_at) AS last_at FROM broadcasts WHERE sender_id=? AND created_at > ?",
            (sender_id, now - 3600),
        ) as cursor:
            rl = await cursor.fetchone()
        if rl["last_at"] is not None and (now - rl["last_at"]) < min_interval:
            wait = int(min_interval - (now - rl["last_at"])) + 1
            raise ValueError(f"Broadcast cooldown active — wait ~{wait}s before broadcasting again")
        if (rl["n"] or 0) >= hourly_cap:
            raise ValueError(f"Broadcast hourly cap reached ({hourly_cap}/hour) — try again later")

        # Eligible recipients: every non-offline agent (online + stale), the sender included (BD5).
        async with db.execute("SELECT id, last_seen, status FROM agents") as cursor:
            agents = await cursor.fetchall()
        skipped_offline = sum(1 for a in agents if a["status"] == "offline")
        eligible = [a for a in agents if a["status"] != "offline"]

        # Safety ceiling: never fan out to more than max_recipients. Realistically unreachable
        # on a localhost hub; if hit, serve the most-recently-active first and report the drop
        # (never silently — AHB-1 caps note).
        skipped_over_cap = 0
        if len(eligible) > max_recipients:
            eligible.sort(key=lambda a: a["last_seen"] or 0, reverse=True)
            skipped_over_cap = len(eligible) - max_recipients
            eligible = eligible[:max_recipients]

        rows = [
            (
                str(uuid.uuid4()), broadcast_id, None, "announcement", sender_id, a["id"],
                payload, context, None, "pending",
                1 if (a["last_seen"] is not None and now - a["last_seen"] > stale_threshold) else 0,
                now, now, subject,
            )
            for a in eligible
        ]
        if rows:
            await db.executemany("""
                INSERT INTO messages (
                    id, session_id, parent_id, kind, sender_id, recipient_id,
                    payload, context, response, status, flagged_stale, created_at, updated_at, subject
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)

        # Audit row (also the rate-limit source) — recorded even on a zero-recipient broadcast,
        # so broadcasting into an empty hub still counts against the caps.
        await db.execute(
            "INSERT INTO broadcasts (id, sender_id, subject, payload, recipient_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (broadcast_id, sender_id, subject, payload, len(rows), now),
        )
        await db.commit()

    return {
        "broadcast_id": broadcast_id,
        "delivered": len(rows),
        "recipients": [a["id"] for a in eligible],
        "skipped_offline": skipped_offline,
        "skipped_over_cap": skipped_over_cap,
    }

@retry_on_lock()
async def claim_pending(db_path, agent_id, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        
        query = """
            UPDATE messages 
            SET status = 'in_progress', claimed_at = ?, updated_at = ?
            WHERE recipient_id = ? AND (
                status = 'pending' OR 
                (status = 'in_progress' AND claimed_at < ?)
            )
            RETURNING *
        """
        async with db.execute(query, (now, now, agent_id, cutoff)) as cursor:
            rows = await cursor.fetchall()
            claimed = [dict(r) for r in rows]
            
        noack_ids = [r["id"] for r in claimed if r["kind"] in NO_ACK_KINDS]
        if noack_ids:
            placeholders = ",".join("?" for _ in noack_ids)
            await db.execute(f"UPDATE messages SET status='completed' WHERE id IN ({placeholders})", noack_ids)
            
        await db.commit()
        return claimed

@retry_on_lock()
async def reclaim_stale(db_path, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress' AND claimed_at < ?
        """, (cutoff,))
        await db.commit()

@retry_on_lock()
async def reset_stuck(db_path):
    async with _connect(db_path) as db:
        cursor = await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress'
        """)
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount

@retry_on_lock()
async def request_input(db_path, message_id, question):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
                
        now = time.time()
        await db.execute("UPDATE messages SET status='input_required', updated_at=? WHERE id=?", (now, message_id))
        await db.commit()
        
    res = await enqueue_message(
        db_path, 
        sender_id=row["recipient_id"],
        recipient_id=row["sender_id"],
        payload=question,
        session_id=row["session_id"],
        parent_id=message_id,
        kind="input_request",
        internal=True,
    )
    return {"request_message_id": res["message_id"], "session_id": res["session_id"]}

@retry_on_lock()
async def complete_message(db_path, message_id, response):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, parent_id, kind, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
                
        now = time.time()
        await db.execute("UPDATE messages SET status='completed', response=?, updated_at=? WHERE id=?", (response, now, message_id))
            
        if row["kind"] == "input_request" and row["parent_id"]:
            # Un-park the parent task ONLY if it is still awaiting this clarification.
            # A duplicate/late reply (at-least-once redelivery, or the requester answering
            # twice) must not revive a parent that already moved on — that would reopen a
            # completed task as duplicate work. (AHB-12)
            async with db.execute("SELECT status, context FROM messages WHERE id=?", (row["parent_id"],)) as cursor:
                parent = await cursor.fetchone()
            if parent and parent["status"] == "input_required":
                new_context = (parent["context"] or "") + f"\n[Clarification Answer]: {response}"
                await db.execute("UPDATE messages SET status='pending', claimed_at=NULL, context=?, updated_at=? WHERE id=?", (new_context, now, row["parent_id"]))
            
        await db.commit()
        
    if row["kind"] == "task":
        # Result fan-out
        await enqueue_message(
            db_path,
            sender_id=row["recipient_id"],
            recipient_id=row["sender_id"],
            payload="",
            response=response,
            session_id=row["session_id"],
            parent_id=message_id,
            kind="result",
            internal=True,
        )

@retry_on_lock()
async def fail_message(db_path, message_id, error):
    async with _connect(db_path) as db:
        async with db.execute("SELECT session_id, parent_id, kind, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")

        now = time.time()
        await db.execute("UPDATE messages SET status='failed', response=?, updated_at=? WHERE id=?", (error, now, message_id))

        if row["kind"] == "input_request" and row["parent_id"]:
            # Failing a clarification (the sender can't/won't answer) must NOT strand the
            # parent in input_required forever — the TTL sweep only touches pending kind='task'
            # (D24). Hand the parent back to the worker as pending, with the refusal noted in
            # context, so the worker re-claims it and decides whether to proceed best-effort or
            # fail the task itself. Symmetric to complete_message's un-park, and idempotent:
            # only if the parent is still parked (a duplicate/late fail is a no-op). (AHB-13 #4)
            async with db.execute("SELECT status, context FROM messages WHERE id=?", (row["parent_id"],)) as cursor:
                parent = await cursor.fetchone()
            if parent and parent["status"] == "input_required":
                new_context = (parent["context"] or "") + f"\n[Clarification Failed]: {error}"
                await db.execute("UPDATE messages SET status='pending', claimed_at=NULL, context=?, updated_at=? WHERE id=?", (new_context, now, row["parent_id"]))

        await db.commit()

    if row["kind"] == "task":
        # Failure fan-out (mirror of the D20 result fan-out for success): surface the task's
        # terminal failure to the sender's live inbox so a peer long-polling check_inbox gets a
        # signal instead of waiting to its idle cap — the live loop never falls back to
        # check_status. Ack-less kind='failure' (auto-completed on claim, NO_ACK_KINDS);
        # internal=True so it survives an offline/unknown/departed sender (D30/AHB-11). (AHB-13 #3)
        await enqueue_message(
            db_path,
            sender_id=row["recipient_id"],
            recipient_id=row["sender_id"],
            payload="",
            response=error,
            session_id=row["session_id"],
            parent_id=message_id,
            kind="failure",
            internal=True,
        )

@retry_on_lock()
async def get_message_endpoints(db_path, message_id):
    """Return {'sender_id', 'recipient_id'} for a message, or None if it doesn't exist.

    Used by the activity-feed middleware to attribute the message-id-only tools
    (reply/fail/request_input/check_status) to a real agent for display, since those
    calls carry no agent_id/sender_id arg (AHB-14). Does NOT touch last_seen (D23).
    """
    async with _connect(db_path) as db:
        async with db.execute("SELECT sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

@retry_on_lock()
async def get_status(db_path, message_id):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
            return dict(row)

@retry_on_lock()
async def peek_inbox(db_path, agent_id, visibility_timeout=VISIBILITY_TIMEOUT):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        
        query = """
            SELECT sender_id FROM messages 
            WHERE recipient_id = ? AND (
                status = 'pending' OR 
                (status = 'in_progress' AND claimed_at < ?)
            )
        """
        async with db.execute(query, (agent_id, cutoff)) as cursor:
            rows = await cursor.fetchall()
            senders = list(set([r["sender_id"] for r in rows]))
            return {"count": len(rows), "senders": senders}

@retry_on_lock()
async def expire_messages(db_path, message_ttl=MESSAGE_TTL):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - message_ttl
        # Sweep unclaimed 'task' AND 'announcement' rows (D24, extended by AHB-1). Both have no
        # dependent parent, so expiring them strands nothing; a never-claimed announcement must
        # not linger forever. input_request/result/failure stay excluded (D24 carve-out).
        await db.execute("""
            UPDATE messages
            SET status = 'expired', updated_at = ?
            WHERE status = 'pending' AND kind IN ('task', 'announcement') AND created_at < ?
        """, (now, cutoff))
        await db.commit()

@retry_on_lock()
async def get_recent_messages(db_path, limit=100):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

@retry_on_lock()
async def get_stats(db_path):
    async with _connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
            total_messages = row[0]
            return {"total_messages": total_messages}

@retry_on_lock()
async def delete_old(db_path):
    async with _connect(db_path) as db:
        cursor = await db.execute("""
            DELETE FROM messages 
            WHERE status IN ('completed', 'failed', 'expired')
        """)
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount
