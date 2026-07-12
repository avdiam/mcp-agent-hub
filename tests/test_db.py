import pytest
import pytest_asyncio
import time
import json
import sqlite3
import aiosqlite

import mcp_hub.db as db

@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = str(tmp_path / "test_hub.db")
    await db.init_db(db_file)
    return db_file

@pytest.mark.asyncio
async def test_sweep_functions_report_change_counts(temp_db):
    # D38: the background sweeper pushes a dashboard update only when a sweep pass
    # actually changed rows — so the sweeps must report how many rows they touched.
    assert await db.reclaim_stale(temp_db, visibility_timeout=600) == 0
    assert await db.expire_messages(temp_db, message_ttl=86400) == 0

    await db.upsert_agent(temp_db, "worker", "[]")
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.enqueue_message(temp_db, "sender", "worker", "will go stale in flight")
    await db.claim_pending(temp_db, "worker")
    await db.enqueue_message(temp_db, "sender", "worker", "will expire unclaimed")

    async with aiosqlite.connect(temp_db) as conn:
        past = time.time() - 90000
        await conn.execute("UPDATE messages SET created_at=?, claimed_at=?", (past, past))
        await conn.commit()

    # One in_progress row reclaimed; then both (now-pending) tasks expire.
    assert await db.reclaim_stale(temp_db, visibility_timeout=600) == 1
    assert await db.expire_messages(temp_db, message_ttl=86400) == 2

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
async def test_delete_agent_option_a_keeps_messages(temp_db):
    await db.upsert_agent(temp_db, "victim", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    msg = await db.enqueue_message(temp_db, "peer", "victim", "hello")

    # Option A (default): agent row removed, messages preserved
    result = await db.delete_agent(temp_db, "victim")
    assert result == {"agents_deleted": 1, "messages_deleted": 0, "offers_deleted": 0,
                      "broadcasts_deleted": 0}

    agents = await db.get_all_agents(temp_db)
    assert [a["id"] for a in agents] == ["peer"]

    # The historical message is still present
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM messages WHERE id=?", (msg["message_id"],)) as cursor:
            assert (await cursor.fetchone())[0] == 1

    # Deleting a non-existent agent reports zero rows removed
    assert (await db.delete_agent(temp_db, "ghost"))["agents_deleted"] == 0

@pytest.mark.asyncio
async def test_catchup_delivers_missed_broadcast(temp_db):
    # AHB-1 P2: an agent registering AFTER a broadcast still receives it, faithfully
    # (payload, subject, context, session_id=broadcast_id), as a normal announcement.
    await db.upsert_agent(temp_db, "sender", "[]")
    res = await db.broadcast(temp_db, "sender", "hub news", subject="motd", context="ctx")

    await db.upsert_agent(temp_db, "late", "[]")
    assert await db.deliver_missed_broadcasts(temp_db, "late") == 1

    claimed = await db.claim_pending(temp_db, "late")
    assert len(claimed) == 1
    ann = claimed[0]
    assert ann["kind"] == "announcement"
    assert ann["payload"] == "hub news"
    assert ann["subject"] == "motd"
    assert ann["context"] == "ctx"
    assert ann["session_id"] == res["broadcast_id"]
    # Ack-less: auto-completed on claim, no redelivery
    assert await db.claim_pending(temp_db, "late") == []

@pytest.mark.asyncio
async def test_catchup_is_idempotent_and_skips_prior_recipients(temp_db):
    # AHB-1 P2: dedupe is structural (a messages row with session_id=broadcast_id exists),
    # so neither original recipients nor already-caught-up agents ever get a duplicate.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "present", "[]")
    await db.broadcast(temp_db, "sender", "news")

    # 'present' got the P1 fan-out row (even while unclaimed) — catch-up adds nothing
    assert await db.deliver_missed_broadcasts(temp_db, "present") == 0

    # late joiner: first catch-up delivers, repeat re-registers are no-ops,
    # including after the delivered row was claimed (auto-completed)
    await db.upsert_agent(temp_db, "late", "[]")
    assert await db.deliver_missed_broadcasts(temp_db, "late") == 1
    assert await db.deliver_missed_broadcasts(temp_db, "late") == 0
    await db.claim_pending(temp_db, "late")
    assert await db.deliver_missed_broadcasts(temp_db, "late") == 0

@pytest.mark.asyncio
async def test_catchup_respects_window(temp_db):
    # AHB-1 P2: broadcasts older than BROADCAST_CATCHUP_WINDOW are not re-delivered.
    await db.upsert_agent(temp_db, "sender", "[]")
    res = await db.broadcast(temp_db, "sender", "old news")

    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute(
            "UPDATE broadcasts SET created_at=? WHERE id=?",
            (time.time() - db.BROADCAST_CATCHUP_WINDOW - 60, res["broadcast_id"]),
        )
        await conn.commit()

    await db.upsert_agent(temp_db, "late", "[]")
    assert await db.deliver_missed_broadcasts(temp_db, "late") == 0

@pytest.mark.asyncio
async def test_catchup_covers_agent_offline_at_broadcast(temp_db):
    # AHB-1 P2: an explicitly-offline agent is skipped by the fan-out (BD3) but catches
    # up when it comes back within the window.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "sleeper", "[]")
    await db.set_agent_offline(temp_db, "sleeper")

    await db.broadcast(temp_db, "sender", "while you were out")

    await db.upsert_agent(temp_db, "sleeper", "[]")  # comes back online
    assert await db.deliver_missed_broadcasts(temp_db, "sleeper") == 1
    claimed = await db.claim_pending(temp_db, "sleeper")
    assert [m["payload"] for m in claimed] == ["while you were out"]

def test_derive_status_edge_cases():
    # AHB-15/D34: liveness derives from last_seen age; the stored column only wins for
    # explicit offline.
    now = 1000.0
    assert db.derive_status("online", now - 1, now) == "online"
    assert db.derive_status("online", now - db.STALE_THRESHOLD - 1, now) == "stale"
    # Sticky offline is preserved even with fresh last_seen
    assert db.derive_status("offline", now - 1, now) == "offline"
    # Defensive: a missing last_seen reads stale, not a crash
    assert db.derive_status("online", None, now) == "stale"

@pytest.mark.asyncio
async def test_get_all_agents_derives_status(temp_db):
    # AHB-15/D34: get_all_agents must not return the stored sticky 'online' for a
    # long-idle agent — status is liveness-derived at the single shared source.
    await db.upsert_agent(temp_db, "fresh", "[]")
    await db.upsert_agent(temp_db, "idle", "[]")
    await db.upsert_agent(temp_db, "gone", "[]")
    await db.set_agent_offline(temp_db, "gone")

    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute(
            "UPDATE agents SET last_seen=? WHERE id=?",
            (time.time() - db.STALE_THRESHOLD - 60, "idle"),
        )
        await conn.commit()

    statuses = {a["id"]: a["status"] for a in await db.get_all_agents(temp_db)}
    assert statuses == {"fresh": "online", "idle": "stale", "gone": "offline"}

@pytest.mark.asyncio
async def test_delete_agent_option_b_purges_messages(temp_db):
    await db.upsert_agent(temp_db, "victim", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    await db.enqueue_message(temp_db, "peer", "victim", "to victim")     # victim as recipient
    sent = await db.enqueue_message(temp_db, "peer", "peer", "self note")  # unrelated
    await db.claim_pending(temp_db, "peer")
    await db.complete_message(temp_db, sent["message_id"], "ok")  # produces a result to peer

    # Option B: also delete every message the victim sent or received
    result = await db.delete_agent(temp_db, "victim", purge_messages=True)
    assert result["agents_deleted"] == 1
    assert result["messages_deleted"] == 1  # only the one addressed to victim

    # Unrelated peer<->peer messages survive
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_id='victim' OR recipient_id='victim'"
        ) as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM messages") as cursor:
            assert (await cursor.fetchone())[0] >= 1

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

@pytest.mark.asyncio
async def test_result_fanout_survives_offline_sender(temp_db):
    # AHB-11: completing a task must NOT raise even if the original sender has since
    # gone offline — the result is still delivered to their inbox (best-effort).
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    await db.claim_pending(temp_db, "worker")
    await db.set_agent_offline(temp_db, "requester")

    # Previously raised "Recipient requester is offline"; must now succeed.
    await db.complete_message(temp_db, task["message_id"], "done")

    assert (await db.get_status(temp_db, task["message_id"]))["status"] == "completed"

    # The result was still enqueued for the (now offline) requester.
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute(
            "SELECT response FROM messages WHERE recipient_id='requester' AND kind='result'"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == "done"

@pytest.mark.asyncio
async def test_result_fanout_survives_unknown_sender(temp_db):
    # AHB-11: the original sender was never registered (or was deleted) — completion
    # must still succeed rather than crash the worker's reply.
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "ghost-sender", "worker", "do work")
    await db.claim_pending(temp_db, "worker")

    await db.complete_message(temp_db, task["message_id"], "done")  # ghost-sender is unknown
    assert (await db.get_status(temp_db, task["message_id"]))["status"] == "completed"

@pytest.mark.asyncio
async def test_request_input_survives_offline_sender(temp_db):
    # AHB-11: asking for clarification fans an input_request back to the original
    # sender; that internal delivery must also bypass the offline guard.
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    await db.claim_pending(temp_db, "worker")
    await db.set_agent_offline(temp_db, "requester")

    # Previously raised "Recipient requester is offline".
    req = await db.request_input(temp_db, task["message_id"], "which color?")
    assert req["request_message_id"]

@pytest.mark.asyncio
async def test_duplicate_input_reply_does_not_revive_completed_parent(temp_db):
    # AHB-12: a duplicate/late reply to an input_request must not reopen a parent task
    # that has already moved past input_required.
    await db.upsert_agent(temp_db, "req", "[]")
    await db.upsert_agent(temp_db, "wrk", "[]")
    task = await db.enqueue_message(temp_db, "req", "wrk", "task")
    tid = task["message_id"]
    await db.claim_pending(temp_db, "wrk")
    r = await db.request_input(temp_db, tid, "which?")
    rid = r["request_message_id"]
    await db.claim_pending(temp_db, "req")
    await db.complete_message(temp_db, rid, "answer")      # first reply un-parks parent
    await db.claim_pending(temp_db, "wrk")                 # worker reclaims parent
    await db.complete_message(temp_db, tid, "task done")   # worker completes parent
    assert (await db.get_status(temp_db, tid))["status"] == "completed"

    # Duplicate reply to the SAME input_request must NOT revive the completed parent.
    await db.complete_message(temp_db, rid, "answer again")
    assert (await db.get_status(temp_db, tid))["status"] == "completed"

@pytest.mark.asyncio
async def test_fail_task_notifies_sender(temp_db):
    # AHB-13 #3: failing a task must surface a 'failure' message to the sender's inbox
    # (the mirror of the 'result' fan-out on success), so a live check_inbox loop gets a
    # signal instead of waiting to its idle cap.
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    await db.claim_pending(temp_db, "worker")

    await db.fail_message(temp_db, task["message_id"], "ran out of budget")
    assert (await db.get_status(temp_db, task["message_id"]))["status"] == "failed"

    # The sender receives a kind='failure' carrying the error...
    fail_claimed = await db.claim_pending(temp_db, "requester")
    assert len(fail_claimed) == 1
    assert fail_claimed[0]["kind"] == "failure"
    assert fail_claimed[0]["response"] == "ran out of budget"
    assert fail_claimed[0]["parent_id"] == task["message_id"]

    # ...and it is ack-less: auto-completed on claim, so it isn't redelivered.
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT status FROM messages WHERE id=?", (fail_claimed[0]["id"],)) as cursor:
            assert (await cursor.fetchone())[0] == "completed"
    assert await db.claim_pending(temp_db, "requester") == []

@pytest.mark.asyncio
async def test_fail_notification_survives_offline_sender(temp_db):
    # AHB-13 #3 + AHB-11: the failure fan-out is an internal delivery, so it must not raise
    # even if the original sender has gone offline.
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    await db.claim_pending(temp_db, "worker")
    await db.set_agent_offline(temp_db, "requester")

    await db.fail_message(temp_db, task["message_id"], "nope")  # must not raise
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute(
            "SELECT response FROM messages WHERE recipient_id='requester' AND kind='failure'"
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == "nope"

@pytest.mark.asyncio
async def test_fail_input_request_returns_parent_to_pending(temp_db):
    # AHB-13 #4: if the sender fails (can't answer) a clarification, the parent task must be
    # handed back to the worker as pending with the refusal noted — not stranded in
    # input_required forever (the TTL sweep never touches it there).
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    tid = task["message_id"]
    await db.claim_pending(temp_db, "worker")
    req = await db.request_input(temp_db, tid, "which color?")
    await db.claim_pending(temp_db, "requester")

    # Sender fails the clarification instead of answering it.
    await db.fail_message(temp_db, req["request_message_id"], "I don't know either")

    # Parent is pending again and re-claimable by the worker, with the failure in context.
    assert (await db.get_status(temp_db, tid))["status"] == "pending"
    worker_claimed = await db.claim_pending(temp_db, "worker")
    assert len(worker_claimed) == 1
    assert worker_claimed[0]["id"] == tid
    assert "Clarification Failed" in worker_claimed[0]["context"]

    # Failing an input_request does NOT itself fan out a task-failure to the sender.
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM messages WHERE kind='failure'") as cursor:
            assert (await cursor.fetchone())[0] == 0

@pytest.mark.asyncio
async def test_fail_input_request_unpark_is_idempotent(temp_db):
    # AHB-13 #4 (mirror of AHB-12): a duplicate/late fail of the same clarification must not
    # revive a parent that has already moved on.
    await db.upsert_agent(temp_db, "requester", "[]")
    await db.upsert_agent(temp_db, "worker", "[]")
    task = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    tid = task["message_id"]
    await db.claim_pending(temp_db, "worker")
    req = await db.request_input(temp_db, tid, "which?")
    rid = req["request_message_id"]
    await db.claim_pending(temp_db, "requester")
    await db.fail_message(temp_db, rid, "can't answer")   # first fail un-parks parent
    await db.claim_pending(temp_db, "worker")             # worker reclaims parent
    await db.complete_message(temp_db, tid, "did my best")  # worker completes it
    assert (await db.get_status(temp_db, tid))["status"] == "completed"

    # Duplicate fail of the SAME clarification must NOT reopen the completed parent.
    await db.fail_message(temp_db, rid, "still can't")
    assert (await db.get_status(temp_db, tid))["status"] == "completed"

@pytest.mark.asyncio
async def test_enqueue_respects_stale_threshold(temp_db):
    # AHB-14: the stale cutoff is a parameter sourced from the single STALE_THRESHOLD
    # constant, not a hardcoded 90 — so tuning it actually changes the flag.
    await db.upsert_agent(temp_db, "recipient", "[]")
    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute("UPDATE agents SET last_seen=?", (time.time() - 120,))
        await conn.commit()

    m1 = await db.enqueue_message(temp_db, "s", "recipient", "p1")                       # 120s old vs default 90 -> stale
    m2 = await db.enqueue_message(temp_db, "s", "recipient", "p2", stale_threshold=300)  # 120s old vs 300 -> not stale

    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute(
            "SELECT id, flagged_stale FROM messages WHERE id IN (?, ?)",
            (m1["message_id"], m2["message_id"]),
        ) as cursor:
            flags = {r[0]: r[1] for r in await cursor.fetchall()}
    assert flags[m1["message_id"]] == 1
    assert flags[m2["message_id"]] == 0

@pytest.mark.asyncio
async def test_get_message_endpoints(temp_db):
    # AHB-14: activity-feed display attribution resolves the acting agent from the message row.
    await db.upsert_agent(temp_db, "worker", "[]")
    msg = await db.enqueue_message(temp_db, "requester", "worker", "do work")
    assert await db.get_message_endpoints(temp_db, msg["message_id"]) == {
        "sender_id": "requester",
        "recipient_id": "worker",
    }
    assert await db.get_message_endpoints(temp_db, "does-not-exist") is None

# --- AHB-1 P1: broadcast / announce ---

@pytest.mark.asyncio
async def test_broadcast_fans_out_online_and_stale_includes_sender_skips_offline(temp_db):
    # BD3 + BD5: a broadcast reaches every non-offline agent INCLUDING the sender, and skips
    # explicitly-offline peers; stale recipients are flagged like any queued send.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "online_peer", "[]")
    await db.upsert_agent(temp_db, "stale_peer", "[]")
    await db.upsert_agent(temp_db, "offline_peer", "[]")
    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute("UPDATE agents SET last_seen=? WHERE id=?", (time.time() - 200, "stale_peer"))
        await conn.commit()
    await db.set_agent_offline(temp_db, "offline_peer")

    res = await db.broadcast(temp_db, "sender", "hello all", subject="notice")
    assert res["delivered"] == 3
    assert set(res["recipients"]) == {"sender", "online_peer", "stale_peer"}
    assert res["skipped_offline"] == 1
    assert res["skipped_over_cap"] == 0

    expected_stale = {"stale_peer": 1}
    for who in ("sender", "online_peer", "stale_peer"):
        inbox = await db.claim_pending(temp_db, who)
        assert len(inbox) == 1
        assert inbox[0]["kind"] == "announcement"
        assert inbox[0]["payload"] == "hello all"
        assert inbox[0]["subject"] == "notice"
        assert inbox[0]["flagged_stale"] == expected_stale.get(who, 0)
    assert await db.claim_pending(temp_db, "offline_peer") == []

    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute(
            "SELECT sender_id, recipient_count FROM broadcasts WHERE id=?", (res["broadcast_id"],)
        ) as cursor:
            row = await cursor.fetchone()
            assert row is not None and row[0] == "sender" and row[1] == 3

@pytest.mark.asyncio
async def test_broadcast_announcement_is_ack_less(temp_db):
    # BD2: an announcement auto-completes on claim (like a result) — no reply, no redelivery.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    await db.broadcast(temp_db, "sender", "ping")

    claimed = await db.claim_pending(temp_db, "peer")
    assert len(claimed) == 1 and claimed[0]["kind"] == "announcement"
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT status FROM messages WHERE id=?", (claimed[0]["id"],)) as cursor:
            assert (await cursor.fetchone())[0] == "completed"
    assert await db.claim_pending(temp_db, "peer") == []

@pytest.mark.asyncio
async def test_broadcast_payload_cap_rejects_and_inserts_nothing(temp_db):
    # A cap violation is all-or-nothing: raise, and write neither messages nor an audit row.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    with pytest.raises(ValueError, match="payload"):
        await db.broadcast(temp_db, "sender", "x" * (db.BROADCAST_MAX_PAYLOAD + 1))
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM messages") as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM broadcasts") as cursor:
            assert (await cursor.fetchone())[0] == 0

@pytest.mark.asyncio
async def test_broadcast_subject_cap_rejects(temp_db):
    await db.upsert_agent(temp_db, "sender", "[]")
    with pytest.raises(ValueError, match="subject"):
        await db.broadcast(temp_db, "sender", "hi", subject="s" * (db.BROADCAST_MAX_SUBJECT + 1))

@pytest.mark.asyncio
async def test_broadcast_cooldown_rejects_second(temp_db):
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.broadcast(temp_db, "sender", "first")
    with pytest.raises(ValueError, match="cooldown"):
        await db.broadcast(temp_db, "sender", "second")
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM broadcasts") as cursor:
            assert (await cursor.fetchone())[0] == 1  # only the first was recorded

@pytest.mark.asyncio
async def test_broadcast_hourly_cap_rejects(temp_db):
    # Override the cooldown to 0 to isolate the hourly cap; small cap keeps it fast.
    await db.upsert_agent(temp_db, "sender", "[]")
    for i in range(3):
        await db.broadcast(temp_db, "sender", f"m{i}", min_interval=0, hourly_cap=3)
    with pytest.raises(ValueError, match="hourly"):
        await db.broadcast(temp_db, "sender", "over", min_interval=0, hourly_cap=3)

@pytest.mark.asyncio
async def test_broadcast_unclaimed_announcement_expires(temp_db):
    # AHB-1 extends the D24 sweep to unclaimed announcements so they don't linger forever.
    await db.upsert_agent(temp_db, "sender", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    await db.broadcast(temp_db, "sender", "old news")
    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute("UPDATE messages SET created_at=?", (time.time() - 90000,))
        await conn.commit()
    await db.expire_messages(temp_db, message_ttl=86400)
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT DISTINCT status FROM messages WHERE kind='announcement'") as cursor:
            statuses = [r[0] for r in await cursor.fetchall()]
    assert statuses == ["expired"]

# --- AHB-2: job-offer board ---

async def _age_broadcast_cooldown(db_file):
    # post_offer broadcasts under the poster's own flood caps; tests that post repeatedly
    # age the cooldown out, same trick as the catch-up window tests.
    async with aiosqlite.connect(db_file) as conn:
        await conn.execute("UPDATE broadcasts SET created_at = created_at - 61")
        await conn.commit()

@pytest.mark.asyncio
async def test_post_offer_creates_row_and_advert(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "watcher", "[]")
    res = await db.post_offer(temp_db, "poster", "build a parser", subject="parser work",
                              required_skills=["python"])
    offer_id = res["offer_id"]

    offers = await db.list_offers(temp_db)
    assert len(offers) == 1
    o = offers[0]
    assert o["id"] == offer_id
    assert o["status"] == "open"
    assert o["poster_id"] == "poster"
    assert o["payload"] == "build a parser"
    assert o["required_skills"] == ["python"]
    assert o["claims"] == []
    assert o["expires_at"] > time.time()

    # The advert broadcast reached the watcher, tagged machine-parseably in context.
    inbox = await db.claim_pending(temp_db, "watcher")
    ads = [m for m in inbox if m["kind"] == "announcement"]
    assert len(ads) == 1
    assert offer_id in ads[0]["payload"]
    assert ads[0]["context"] == f"job_offer:{offer_id}"
    assert ads[0]["subject"].startswith("[job]")

@pytest.mark.asyncio
async def test_post_offer_caps_reject_and_insert_nothing(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    with pytest.raises(ValueError, match="not a registered agent"):
        await db.post_offer(temp_db, "ghost", "work")
    with pytest.raises(ValueError, match="payload"):
        await db.post_offer(temp_db, "poster", "x" * (db.OFFER_MAX_PAYLOAD + 1))
    with pytest.raises(ValueError, match="subject"):
        await db.post_offer(temp_db, "poster", "work", subject="s" * (db.OFFER_MAX_SUBJECT + 1))
    with pytest.raises(ValueError, match="empty"):
        await db.post_offer(temp_db, "poster", "   ")
    assert await db.list_offers(temp_db) == []
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM broadcasts") as cursor:
            assert (await cursor.fetchone())[0] == 0  # rejections never broadcast

    # Open-offer cap: the 6th open offer from one poster is rejected (checked BEFORE the
    # broadcast, so it consumes no broadcast budget either).
    for i in range(db.OFFER_MAX_OPEN_PER_POSTER):
        await db.post_offer(temp_db, "poster", f"work {i}")
        await _age_broadcast_cooldown(temp_db)
    with pytest.raises(ValueError, match="Open-offer cap"):
        await db.post_offer(temp_db, "poster", "one too many")
    assert len(await db.list_offers(temp_db, status="open")) == db.OFFER_MAX_OPEN_PER_POSTER

@pytest.mark.asyncio
async def test_post_offer_shares_broadcast_cooldown(temp_db):
    # Posting an offer IS a broadcast: offer spam and broadcast spam share one budget,
    # and a cooldown rejection posts no offer row (all-or-nothing).
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.post_offer(temp_db, "poster", "first")
    with pytest.raises(ValueError, match="cooldown"):
        await db.post_offer(temp_db, "poster", "second too soon")
    assert len(await db.list_offers(temp_db)) == 1

@pytest.mark.asyncio
async def test_claim_offer_accumulates_and_notifies_poster(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    await db.upsert_agent(temp_db, "bob", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work", subject="w"))["offer_id"]

    c1 = await db.claim_offer(temp_db, "alice", offer_id, note="I know parsers")
    assert c1["pending_claims"] == 1
    c2 = await db.claim_offer(temp_db, "bob", offer_id)
    assert c2["pending_claims"] == 2

    # Auction model: the offer stays open while claims accumulate — no first-claim lock.
    o = (await db.list_offers(temp_db, status="open"))[0]
    assert [c["claimant_id"] for c in o["claims"]] == ["alice", "bob"]

    # Poster is notified with ack-less offer_updates, session-threaded to the offer.
    inbox = await db.claim_pending(temp_db, "poster")
    ups = [m for m in inbox if m["kind"] == "offer_update"]
    assert len(ups) == 2
    assert all(m["session_id"] == offer_id for m in ups)
    assert "I know parsers" in ups[0]["payload"]
    # Ack-less: auto-completed on claim, never redelivered.
    again = await db.claim_pending(temp_db, "poster")
    assert [m for m in again if m["kind"] == "offer_update"] == []

@pytest.mark.asyncio
async def test_claim_offer_guards(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]

    with pytest.raises(ValueError, match="not found"):
        await db.claim_offer(temp_db, "alice", "no-such-offer")
    with pytest.raises(ValueError, match="your own offer"):
        await db.claim_offer(temp_db, "poster", offer_id)
    with pytest.raises(ValueError, match="not a registered agent"):
        await db.claim_offer(temp_db, "ghost", offer_id)
    with pytest.raises(ValueError, match="note exceeds"):
        await db.claim_offer(temp_db, "alice", offer_id, note="n" * (db.OFFER_MAX_NOTE + 1))
    await db.claim_offer(temp_db, "alice", offer_id)
    with pytest.raises(ValueError, match="already have a pending claim"):
        await db.claim_offer(temp_db, "alice", offer_id)

@pytest.mark.asyncio
async def test_resolve_select_assigns_creates_task_rejects_losers(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    await db.upsert_agent(temp_db, "bob", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "build it", subject="build"))["offer_id"]
    await db.claim_offer(temp_db, "alice", offer_id)
    await db.claim_offer(temp_db, "bob", offer_id)

    res = await db.resolve_offer(temp_db, "poster", offer_id, "select", claimant_id="alice")
    assert res["status"] == "assigned"
    assert res["rejected_claims"] == 1
    task_id = res["task_message_id"]

    o = (await db.list_offers(temp_db))[0]
    assert o["status"] == "assigned"
    assert o["claimant_id"] == "alice"
    assert o["task_message_id"] == task_id
    assert {c["claimant_id"]: c["status"] for c in o["claims"]} == {
        "alice": "selected", "bob": "rejected"}

    # The winner's "you got it" signal IS a normal ackable task with the full payload,
    # session-threaded to the offer.
    tasks = [m for m in await db.claim_pending(temp_db, "alice") if m["kind"] == "task"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == task_id
    assert tasks[0]["payload"] == "build it"
    assert tasks[0]["session_id"] == offer_id
    assert tasks[0]["sender_id"] == "poster"

    # The loser is told, ack-lessly.
    ups = [m for m in await db.claim_pending(temp_db, "bob") if m["kind"] == "offer_update"]
    assert len(ups) == 1
    assert "not selected" in ups[0]["payload"]

    # Completing the task fans the result back to the poster via the existing machinery.
    await db.complete_message(temp_db, task_id, "done!")
    results = [m for m in await db.claim_pending(temp_db, "poster") if m["kind"] == "result"]
    assert len(results) == 1
    assert results[0]["response"] == "done!"
    assert results[0]["session_id"] == offer_id

@pytest.mark.asyncio
async def test_resolve_offer_guards(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]

    with pytest.raises(ValueError, match="not found"):
        await db.resolve_offer(temp_db, "poster", "no-such-offer", "withdraw")
    with pytest.raises(ValueError, match="Only the poster"):
        await db.resolve_offer(temp_db, "alice", offer_id, "withdraw")
    with pytest.raises(ValueError, match="requires claimant_id"):
        await db.resolve_offer(temp_db, "poster", offer_id, "select")
    with pytest.raises(ValueError, match="no pending claim"):
        await db.resolve_offer(temp_db, "poster", offer_id, "select", claimant_id="alice")
    with pytest.raises(ValueError, match="must be 'select' or 'withdraw'"):
        await db.resolve_offer(temp_db, "poster", offer_id, "explode")

@pytest.mark.asyncio
async def test_withdraw_rejects_and_notifies_claimants(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]
    await db.claim_offer(temp_db, "alice", offer_id)

    res = await db.resolve_offer(temp_db, "poster", offer_id, "withdraw")
    assert res["status"] == "withdrawn"
    o = (await db.list_offers(temp_db))[0]
    assert o["status"] == "withdrawn"
    assert all(c["status"] == "rejected" for c in o["claims"])

    ups = [m for m in await db.claim_pending(temp_db, "alice") if m["kind"] == "offer_update"]
    assert any("withdrawn" in m["payload"] for m in ups)

    # A withdrawn offer is terminal: no more claims, no re-resolution.
    with pytest.raises(ValueError, match="not open"):
        await db.claim_offer(temp_db, "alice", offer_id)
    with pytest.raises(ValueError, match="not open"):
        await db.resolve_offer(temp_db, "poster", offer_id, "withdraw")

@pytest.mark.asyncio
async def test_expire_offers_sweep(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]
    await db.claim_offer(temp_db, "alice", offer_id)
    # Drain both inboxes so the asserts below see only the sweep's output.
    await db.claim_pending(temp_db, "alice")
    await db.claim_pending(temp_db, "poster")

    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute("UPDATE job_offers SET expires_at=? WHERE id=?",
                           (time.time() - 1, offer_id))
        await conn.commit()

    # The lazy view reads 'expired' even before the sweep persists it, and the offer no
    # longer shows as open/claimable.
    assert (await db.list_offers(temp_db))[0]["status"] == "expired"
    assert await db.list_offers(temp_db, status="open") == []
    with pytest.raises(ValueError, match="expired"):
        await db.claim_offer(temp_db, "alice", offer_id)

    assert await db.expire_offers(temp_db) == 1
    o = (await db.list_offers(temp_db, status="expired"))[0]
    assert o["claims"][0]["status"] == "rejected"
    ups = [m for m in await db.claim_pending(temp_db, "alice") if m["kind"] == "offer_update"]
    assert len(ups) == 1
    assert "expired" in ups[0]["payload"]
    assert await db.expire_offers(temp_db) == 0  # idempotent

@pytest.mark.asyncio
async def test_failed_assignment_reopens_offer_and_allows_reclaim(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]
    await db.claim_offer(temp_db, "alice", offer_id)
    res = await db.resolve_offer(temp_db, "poster", offer_id, "select", claimant_id="alice")
    task_id = res["task_message_id"]

    await db.claim_pending(temp_db, "alice")  # claim the assignment task
    await db.fail_message(temp_db, task_id, "can not do it after all")

    o = (await db.list_offers(temp_db))[0]
    assert o["status"] == "open"  # back on the board (still within TTL)
    assert o["claimant_id"] is None
    assert o["task_message_id"] is None
    assert {c["status"] for c in o["claims"]} == {"failed"}

    # The poster still gets the normal D31 failure fan-out, threaded to the offer session.
    fails = [m for m in await db.claim_pending(temp_db, "poster") if m["kind"] == "failure"]
    assert len(fails) == 1
    assert fails[0]["session_id"] == offer_id

    # The pending-only unique index lets the same agent claim again.
    again = await db.claim_offer(temp_db, "alice", offer_id)
    assert again["pending_claims"] == 1

@pytest.mark.asyncio
async def test_failed_assignment_past_ttl_expires_instead(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]
    await db.claim_offer(temp_db, "alice", offer_id)
    task_id = (await db.resolve_offer(temp_db, "poster", offer_id, "select",
                                      claimant_id="alice"))["task_message_id"]

    async with aiosqlite.connect(temp_db) as conn:
        await conn.execute("UPDATE job_offers SET expires_at=? WHERE id=?",
                           (time.time() - 1, offer_id))
        await conn.commit()

    await db.claim_pending(temp_db, "alice")
    await db.fail_message(temp_db, task_id, "too late")
    assert (await db.list_offers(temp_db))[0]["status"] == "expired"

@pytest.mark.asyncio
async def test_delete_agent_purge_covers_board_footprint(temp_db):
    # Purging an agent must not leave its board rows behind: offers it posted (and their
    # claims by others) go, and so do its claims on surviving offers.
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    my_offer = (await db.post_offer(temp_db, "poster", "mine"))["offer_id"]
    await _age_broadcast_cooldown(temp_db)
    their_offer = (await db.post_offer(temp_db, "alice", "theirs"))["offer_id"]
    await db.claim_offer(temp_db, "alice", my_offer)
    await db.claim_offer(temp_db, "poster", their_offer)

    res = await db.delete_agent(temp_db, "poster", purge_messages=True)
    assert res["offers_deleted"] == 1

    remaining = await db.list_offers(temp_db)
    assert [o["id"] for o in remaining] == [their_offer]
    assert remaining[0]["claims"] == []  # poster's claim on alice's offer is gone too

    # Non-purge delete leaves the board intact.
    await db.delete_agent(temp_db, "alice", purge_messages=False)
    assert [o["id"] for o in await db.list_offers(temp_db)] == [their_offer]

@pytest.mark.asyncio
async def test_purge_deletes_broadcasts_so_catchup_cannot_requeue_ghost_adverts(temp_db):
    # AHB-16: purging a broadcaster removes its audit rows too — otherwise the purge deletes
    # recipients' advert copies and register-time catch-up re-queues ghosts for dead offers.
    await db.upsert_agent(temp_db, "probe", "[]")
    await db.upsert_agent(temp_db, "peer", "[]")
    await db.post_offer(temp_db, "probe", "smoke work")

    res = await db.delete_agent(temp_db, "probe", purge_messages=True)
    assert res["broadcasts_deleted"] == 1
    assert res["offers_deleted"] == 1

    # peer's advert copy was purged with the sender; without the audit row, re-registering
    # must NOT re-deliver the ghost advert.
    assert await db.deliver_missed_broadcasts(temp_db, "peer") == 0
    async with aiosqlite.connect(temp_db) as conn:
        async with conn.execute("SELECT COUNT(*) FROM broadcasts") as cursor:
            assert (await cursor.fetchone())[0] == 0

@pytest.mark.asyncio
async def test_completed_assignment_marks_offer_completed(temp_db):
    # AHB-17 #3: fulfilling the assignment task flips the offer to terminal 'completed'
    # (the success mirror of the failure re-open); winner's claim stays 'selected'.
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]
    claim = await db.claim_offer(temp_db, "alice", offer_id)
    # AHB-17 #1: the claim return states the outcomes contract.
    assert claim["expires_at"] > time.time()
    assert "inbox" in claim["next"]
    task_id = (await db.resolve_offer(temp_db, "poster", offer_id, "select",
                                      claimant_id="alice"))["task_message_id"]

    await db.claim_pending(temp_db, "alice")
    await db.complete_message(temp_db, task_id, "done")

    o = (await db.list_offers(temp_db, status="completed"))[0]
    assert o["id"] == offer_id
    assert o["claimant_id"] == "alice"
    assert o["claims"][0]["status"] == "selected"
    # Duplicate/late completion of the same task is a no-op (guard on status='assigned'):
    # the offer stays terminal 'completed' and the winning claim stays 'selected'.
    await db.complete_message(temp_db, task_id, "done again")
    o = (await db.list_offers(temp_db, status="completed"))[0]
    assert o["id"] == offer_id
    assert o["claims"][0]["status"] == "selected"

@pytest.mark.asyncio
async def test_ordinary_task_completion_leaves_board_alone(temp_db):
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]

    msg = await db.enqueue_message(temp_db, "poster", "alice", "unrelated task")
    await db.claim_pending(temp_db, "alice")
    await db.complete_message(temp_db, msg["message_id"], "done")
    assert (await db.list_offers(temp_db))[0]["status"] == "open"

@pytest.mark.asyncio
async def test_ordinary_task_failure_leaves_board_alone(temp_db):
    # The re-open hook must be a no-op for tasks that are not job-board assignments.
    await db.upsert_agent(temp_db, "poster", "[]")
    await db.upsert_agent(temp_db, "alice", "[]")
    offer_id = (await db.post_offer(temp_db, "poster", "work"))["offer_id"]

    msg = await db.enqueue_message(temp_db, "poster", "alice", "unrelated task")
    await db.claim_pending(temp_db, "alice")
    await db.fail_message(temp_db, msg["message_id"], "nope")
    assert (await db.list_offers(temp_db))[0]["status"] == "open"
