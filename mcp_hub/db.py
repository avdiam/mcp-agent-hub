import sqlite3
import aiosqlite
import json
import time
import uuid

if sqlite3.sqlite_version_info < (3, 35):
    raise RuntimeError(f"SQLite >= 3.35 is required. Found: {sqlite3.sqlite_version}")

async def init_db(db_path="hub.db"):
    async with aiosqlite.connect(db_path) as db:
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
                updated_at REAL
            )
        """)
        
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_recipient_status ON messages(recipient_id, status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_session ON messages(session_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_created_at ON messages(created_at)")
        
        await db.commit()

async def upsert_agent(db_path, agent_id, skills_json, description=None):
    async with aiosqlite.connect(db_path) as db:
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

async def get_all_agents(db_path):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM agents") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def set_agent_offline(db_path, agent_id):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE agents SET status='offline' WHERE id=?", (agent_id,))
        await db.commit()

async def touch_last_seen(db_path, agent_id):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent_id))
        await db.commit()

async def enqueue_message(db_path, sender_id, recipient_id, payload, context=None, session_id=None, parent_id=None, kind="task", response=None):
    message_id = str(uuid.uuid4())
    if not session_id:
        session_id = str(uuid.uuid4())
        
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status, last_seen FROM agents WHERE id=?", (recipient_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError(f"Recipient {recipient_id} is unknown")
            if row["status"] == "offline":
                raise ValueError(f"Recipient {recipient_id} is offline")
                
            is_stale = 0
            if time.time() - row["last_seen"] > 90:
                is_stale = 1
                
        now = time.time()
        await db.execute("""
            INSERT INTO messages (
                id, session_id, parent_id, kind, sender_id, recipient_id,
                payload, context, response, status, flagged_stale, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """, (message_id, session_id, parent_id, kind, sender_id, recipient_id, payload, context, response, is_stale, now, now))
        await db.commit()
    
    return {"message_id": message_id, "session_id": session_id}

async def claim_pending(db_path, agent_id, visibility_timeout=600):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
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
            
        result_ids = [r["id"] for r in claimed if r["kind"] == "result"]
        if result_ids:
            placeholders = ",".join("?" for _ in result_ids)
            await db.execute(f"UPDATE messages SET status='completed' WHERE id IN ({placeholders})", result_ids)
            
        await db.commit()
        return claimed

async def reclaim_stale(db_path, visibility_timeout=600):
    async with aiosqlite.connect(db_path) as db:
        now = time.time()
        cutoff = now - visibility_timeout
        await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress' AND claimed_at < ?
        """, (cutoff,))
        await db.commit()

async def reset_stuck(db_path):
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("""
            UPDATE messages 
            SET status = 'pending', claimed_at = NULL 
            WHERE status = 'in_progress'
        """)
        rowcount = cursor.rowcount
        await db.commit()
        return rowcount

async def request_input(db_path, message_id, question):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
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
        kind="input_request"
    )
    return {"request_message_id": res["message_id"], "session_id": res["session_id"]}

async def complete_message(db_path, message_id, response):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        now = time.time()
        await db.execute("UPDATE messages SET status='completed', response=?, updated_at=? WHERE id=?", (response, now, message_id))
        
        async with db.execute("SELECT session_id, parent_id, kind, sender_id, recipient_id FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            
        if row["kind"] == "input_request" and row["parent_id"]:
            # Un-park parent task
            async with db.execute("SELECT context FROM messages WHERE id=?", (row["parent_id"],)) as cursor:
                parent = await cursor.fetchone()
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
            kind="result"
        )

async def fail_message(db_path, message_id, error):
    async with aiosqlite.connect(db_path) as db:
        now = time.time()
        await db.execute("UPDATE messages SET status='failed', response=?, updated_at=? WHERE id=?", (error, now, message_id))
        await db.commit()

async def get_status(db_path, message_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM messages WHERE id=?", (message_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                raise ValueError("Message not found")
            return dict(row)

async def peek_inbox(db_path, agent_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
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

async def expire_messages(db_path, message_ttl=86400):
    async with aiosqlite.connect(db_path) as db:
        now = time.time()
        cutoff = now - message_ttl
        await db.execute("""
            UPDATE messages 
            SET status = 'expired', updated_at = ? 
            WHERE status = 'pending' AND kind = 'task' AND created_at < ?
        """, (now, cutoff))
        await db.commit()

async def get_recent_messages(db_path, limit=100):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_stats(db_path):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            row = await cursor.fetchone()
            total_messages = row[0]
            return {"total_messages": total_messages}
