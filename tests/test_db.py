import pytest
import pytest_asyncio
import time
import json
import sqlite3
import aiosqlite

import db

@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = str(tmp_path / "test_hub.db")
    await db.init_db(db_file)
    return db_file

@pytest.mark.asyncio
async def test_skills_json_roundtrip(temp_db):
    skills = [
        {"id": "test_skill", "name": "Test Skill", "description": "A test"}
    ]
    skills_json = json.dumps(skills)
    await db.upsert_agent(temp_db, "agent1", skills_json, "description")
    
    agents = await db.get_all_agents(temp_db)
    assert len(agents) == 1
    assert agents[0]["id"] == "agent1"
    assert json.loads(agents[0]["skills"]) == skills

@pytest.mark.asyncio
async def test_atomic_claim_and_peek(temp_db):
    await db.upsert_agent(temp_db, "agent1", "[]")
    
    # Send messages
    msg1 = await db.enqueue_message(temp_db, "sender1", "agent1", "task 1")
    msg2 = await db.enqueue_message(temp_db, "sender1", "agent1", "task 2")
    
    # Peek should return count=2 and senders list, but not claim
    peek = await db.peek_inbox(temp_db, "agent1")
    assert peek["count"] == 2
    assert "sender1" in peek["senders"]
    
    # Claim should return 2 messages
    claimed = await db.claim_pending(temp_db, "agent1")
    assert len(claimed) == 2
    
    # Subsequent peek should be empty
    peek2 = await db.peek_inbox(temp_db, "agent1")
    assert peek2["count"] == 0
    
    # Subsequent claim should be empty
    claimed2 = await db.claim_pending(temp_db, "agent1")
    assert len(claimed2) == 0

@pytest.mark.asyncio
async def test_visibility_timeout_redelivery(temp_db):
    await db.upsert_agent(temp_db, "agent1", "[]")
    msg = await db.enqueue_message(temp_db, "sender1", "agent1", "payload")
    
    # Claim it
    await db.claim_pending(temp_db, "agent1")
    
    # Manually set claimed_at to in the past to simulate timeout
    async with aiosqlite.connect(temp_db) as conn:
        past = time.time() - 1000
        await conn.execute("UPDATE messages SET claimed_at = ? WHERE id = ?", (past, msg["message_id"]))
        await conn.commit()
        
    # Reclaim stale
    await db.reclaim_stale(temp_db, visibility_timeout=600)
    
    # It should be claimable again
    claimed = await db.claim_pending(temp_db, "agent1")
    assert len(claimed) == 1
    assert claimed[0]["id"] == msg["message_id"]

@pytest.mark.asyncio
async def test_offline_vs_stale_behavior(temp_db):
    await db.upsert_agent(temp_db, "agent_stale", "[]")
    await db.upsert_agent(temp_db, "agent_offline", "[]")
    
    # Make them old
    async with aiosqlite.connect(temp_db) as conn:
        past = time.time() - 200
        await conn.execute("UPDATE agents SET last_seen = ?", (past,))
        await conn.commit()
        
    # Explicitly disconnect offline agent
    await db.set_agent_offline(temp_db, "agent_offline")
    
    # Sending to offline should fail
    with pytest.raises(ValueError, match="offline"):
        await db.enqueue_message(temp_db, "sender", "agent_offline", "payload")
        
    # Sending to stale should succeed but flag as stale
    msg = await db.enqueue_message(temp_db, "sender", "agent_stale", "payload")
    
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT flagged_stale FROM messages WHERE id=?", (msg["message_id"],)) as cursor:
            row = await cursor.fetchone()
            assert row[0] == 1

@pytest.mark.asyncio
async def test_input_required_roundtrip(temp_db):
    await db.upsert_agent(temp_db, "worker", "[]")
    await db.upsert_agent(temp_db, "requester", "[]")
    
    task_msg = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    task_id = task_msg["message_id"]
    
    # Worker claims task
    claimed = await db.claim_pending(temp_db, "worker")
    assert len(claimed) == 1
    
    # Worker asks question
    req = await db.request_input(temp_db, task_id, "what color?")
    req_id = req["request_message_id"]
    
    # Requester checks inbox, gets input_request
    req_claimed = await db.claim_pending(temp_db, "requester")
    assert len(req_claimed) == 1
    assert req_claimed[0]["id"] == req_id
    assert req_claimed[0]["kind"] == "input_request"
    
    # Requester answers
    await db.complete_message(temp_db, req_id, "blue")
    
    # Worker checks inbox, original task is unparked and claimable
    worker_claimed = await db.claim_pending(temp_db, "worker")
    assert len(worker_claimed) == 1
    assert worker_claimed[0]["id"] == task_id
    assert "blue" in worker_claimed[0]["context"]

@pytest.mark.asyncio
async def test_expired_sweep(temp_db):
    await db.upsert_agent(temp_db, "agent1", "[]")
    await db.upsert_agent(temp_db, "sender", "[]")
    msg_inprog = await db.enqueue_message(temp_db, "sender", "agent1", "task2")
    
    # Claim the in_progress one
    await db.claim_pending(temp_db, "agent1")
    
    # Enqueue msg_task AFTER claim so it stays pending
    msg_task = await db.enqueue_message(temp_db, "sender", "agent1", "task")
    
    # Request input to make a parked one
    await db.request_input(temp_db, msg_inprog["message_id"], "q")
    
    # Manually make them all old
    async with aiosqlite.connect(temp_db) as conn:
        past = time.time() - 90000
        await conn.execute("UPDATE messages SET created_at = ?", (past,))
        await conn.commit()
        
    await db.expire_messages(temp_db, message_ttl=86400)
    
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT id, status FROM messages") as cursor:
            rows = await cursor.fetchall()
            statuses = {r[0]: r[1] for r in rows}
            
            # The pending task should be expired
            assert statuses[msg_task["message_id"]] == "expired"
            # The parked task should still be input_required
            assert statuses[msg_inprog["message_id"]] == "input_required"

@pytest.mark.asyncio
async def test_result_fan_out(temp_db):
    await db.upsert_agent(temp_db, "worker", "[]")
    await db.upsert_agent(temp_db, "requester", "[]")
    
    task_msg = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    task_id = task_msg["message_id"]
    
    # Claim and complete
    await db.claim_pending(temp_db, "worker")
    await db.complete_message(temp_db, task_id, "done")
    
    # Check requester inbox for result
    req_claimed = await db.claim_pending(temp_db, "requester")
    assert len(req_claimed) == 1
    assert req_claimed[0]["kind"] == "result"
    assert req_claimed[0]["response"] == "done"
    
    # Ensure the result is auto-completed in the DB after claim (no ack needed)
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT status FROM messages WHERE id=?", (req_claimed[0]["id"],)) as cursor:
            row = await cursor.fetchone()
            assert row[0] == "completed"
