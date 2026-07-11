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
# reply/fail's them, so claim_pending marks them 'completed' on the way out. These are the
# hub-generated fan-outs that report the fate of a task YOU sent:
#   result  = the task succeeded (D20)
#   failure = the task failed     (AHB-13 #3)
# AHB-1's announcement kind will join this set when broadcast lands.
NO_ACK_KINDS = ("result", "failure")



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

@retry_on_lock()
async def get_all_agents(db_path):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM agents") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

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
async def enqueue_message(db_path, sender_id, recipient_id, payload, context=None, session_id=None, parent_id=None, kind="task", response=None, subject=None, internal=False):
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
                if time.time() - row["last_seen"] > 90:
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
async def claim_pending(db_path, agent_id, visibility_timeout=600):
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
async def reclaim_stale(db_path, visibility_timeout=600):
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
async def get_status(db_path, message_id):
    async with _connect(db_path) as db:
        async with db.execute("SELECT * FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
            return dict(row)

@retry_on_lock()
async def peek_inbox(db_path, agent_id):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - 600 # default visibility_timeout
        
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
async def expire_messages(db_path, message_ttl=86400):
    async with _connect(db_path) as db:
        now = time.time()
        cutoff = now - message_ttl
        await db.execute("""
            UPDATE messages 
            SET status = 'expired', updated_at = ? 
            WHERE status = 'pending' AND kind = 'task' AND created_at < ?
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
