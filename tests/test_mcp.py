import pytest
import pytest_asyncio
import asyncio
from httpx import ASGITransport, AsyncClient

from mcp_hub.hub import app, DB_PATH
from mcp_hub.hub import register_agent, send_message, check_inbox, reply_to_message, check_status, request_input, broadcast_message
import mcp_hub.db as db

@pytest_asyncio.fixture
async def test_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        yield client

import mcp_hub.hub as hub

@pytest_asyncio.fixture(autouse=True)
async def setup_db(tmp_path):
    temp_db_path = str(tmp_path / "test_mcp_hub.db")
    
    # Patch the global DB_PATH in the hub module so tools use the temp DB
    original_db_path = hub.DB_PATH
    hub.DB_PATH = temp_db_path
    
    await db.init_db(temp_db_path)
    
    yield
    
    # Restore original DB_PATH
    hub.DB_PATH = original_db_path

@pytest.mark.asyncio
async def test_api_peek(test_client):
    await db.upsert_agent(hub.DB_PATH, "test_agent", "[]")
    await db.enqueue_message(hub.DB_PATH, "sender1", "test_agent", "msg1")
    await db.enqueue_message(hub.DB_PATH, "sender2", "test_agent", "msg2")

    # Needs Origin since it's an /api/ endpoint
    res = await test_client.get("/api/peek?agent_id=test_agent", headers={"Origin": "http://localhost"})
    assert res.status_code == 200
    data = res.json()
    assert data["count"] == 2
    assert "sender1" in data["senders"]

@pytest.mark.asyncio
async def test_api_peek_refreshes_last_seen(test_client):
    # AHB-3: peeking your own inbox should refresh last_seen so a hook-present-but-quiet
    # session doesn't decay to stale between turns.
    import time
    import aiosqlite

    await db.upsert_agent(hub.DB_PATH, "peeker", "[]")

    # Age the agent well past STALE_THRESHOLD.
    stale_ts = time.time() - 1000
    async with aiosqlite.connect(hub.DB_PATH) as conn:
        await conn.execute("UPDATE agents SET last_seen=? WHERE id=?", (stale_ts, "peeker"))
        await conn.commit()

    res = await test_client.get("/api/peek?agent_id=peeker", headers={"Origin": "http://localhost"})
    assert res.status_code == 200

    # last_seen should now be fresh (within the last few seconds), not the aged value.
    async with aiosqlite.connect(hub.DB_PATH) as conn:
        async with conn.execute("SELECT last_seen FROM agents WHERE id=?", ("peeker",)) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert time.time() - row[0] < 5

    # Peeking an unknown agent must not error (no row to update).
    res_unknown = await test_client.get("/api/peek?agent_id=ghost", headers={"Origin": "http://localhost"})
    assert res_unknown.status_code == 200
    assert res_unknown.json()["count"] == 0

@pytest.mark.asyncio
async def test_origin_validation(test_client):
    # Localhost Origin allowed
    res_good = await test_client.get("/mcp", headers={"Origin": "http://localhost:8000"})
    assert res_good.status_code != 403
    
    # Evil Origin rejected
    res_evil = await test_client.get("/mcp", headers={"Origin": "http://evil.com"})
    assert res_evil.status_code == 403

    # Missing Origin, but Sec-Fetch-Site: same-origin allowed (Dashboard case)
    res_missing_same = await test_client.get("/api/state", headers={"Sec-Fetch-Site": "same-origin"})
    assert res_missing_same.status_code == 200

    # Missing Origin, but Sec-Fetch-Site: cross-site rejected
    res_missing_cross = await test_client.get("/api/state", headers={"Sec-Fetch-Site": "cross-site"})
    assert res_missing_cross.status_code == 403

    # Host spoof rejected
    res_spoof = await test_client.get("/mcp", headers={"Host": "evil.com:8000"})
    assert res_spoof.status_code == 403

@pytest.mark.asyncio
async def test_mcp_tool_roundtrip():
    # Register agents
    await register_agent("sender", [], "Sender Agent")
    await register_agent("worker", [], "Worker Agent")

    # Send message
    res_send = await send_message("sender", "worker", "do this")
    msg_id = res_send["message_id"]

    # Check inbox (Worker)
    inbox = await check_inbox("worker", wait=False)
    assert len(inbox) == 1
    assert inbox[0]["payload"] == "do this"
    assert inbox[0]["status"] == "in_progress"

    # Request input
    await request_input(msg_id, "how exactly?")
    
    # Sender checks inbox
    sender_inbox = await check_inbox("sender", wait=False)
    assert len(sender_inbox) == 1
    assert sender_inbox[0]["payload"] == "how exactly?"
    assert sender_inbox[0]["kind"] == "input_request"
    req_id = sender_inbox[0]["id"]
    
    # Sender replies
    await reply_to_message(req_id, "like this")
    
    # Worker checks inbox again (unparked)
    worker_inbox2 = await check_inbox("worker", wait=False)
    assert len(worker_inbox2) == 1
    assert worker_inbox2[0]["id"] == msg_id
    assert "[Clarification Answer]: like this" in worker_inbox2[0]["context"]
    
    # Worker completes
    await reply_to_message(msg_id, "done")
    
    # Sender checks inbox for result (D20 fan-out)
    sender_inbox2 = await check_inbox("sender", wait=False)
    assert len(sender_inbox2) == 1
    assert sender_inbox2[0]["kind"] == "result"
    assert sender_inbox2[0]["response"] == "done"
    
    # Check status
    status = await check_status(msg_id)
    assert status["status"] == "completed"
    assert status["response"] == "done"

@pytest.mark.asyncio
async def test_api_reset(test_client):
    await db.upsert_agent(hub.DB_PATH, "worker", "[]")
    msg = await db.enqueue_message(hub.DB_PATH, "sender", "worker", "stuck_task")
    await db.claim_pending(hub.DB_PATH, "worker", visibility_timeout=600)
    
    hub.activity_buffer.append({"test": "stuck"})
    
    res = await test_client.post("/api/reset", headers={"Origin": "http://localhost:8000"})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["reclaimed_messages"] == 1
    assert data["cleared_events"] == 1
    assert len(hub.activity_buffer) == 0
    
    status = await db.get_status(hub.DB_PATH, msg["message_id"])
    assert status["status"] == "pending"
    assert status["claimed_at"] is None

@pytest.mark.asyncio
async def test_broadcast_message_tool():
    # AHB-1 P1: broadcast_message fans an ack-less announcement to all connected agents.
    await register_agent("caster", [], "Caster")
    await register_agent("listener", [], "Listener")

    res = await broadcast_message("caster", "team standup in 5", subject="standup")
    assert res["ok"] is True
    assert set(res["recipients"]) == {"caster", "listener"}  # BD5: sender echoed
    assert res["delivered"] == 2

    inbox = await check_inbox("listener", wait=False)
    assert len(inbox) == 1
    assert inbox[0]["kind"] == "announcement"
    assert inbox[0]["payload"] == "team standup in 5"
    # Ack-less: no reply/fail, and it is not redelivered.
    assert await check_inbox("listener", wait=False) == []

@pytest.mark.asyncio
async def test_broadcast_message_cap_error():
    # A cap violation returns a clean structured error, not a raw exception.
    await register_agent("caster", [], "Caster")
    res = await broadcast_message("caster", "x" * 5000)  # exceeds the 4096-byte payload cap
    assert res["ok"] is False
    assert "payload" in res["error"]
    assert res["delivered"] == 0

@pytest.mark.asyncio
async def test_tunables_are_single_source():
    # AHB-14: hub re-exports db's tunables (same object) rather than redefining them, so
    # tuning a constant can't silently desync the DB logic from the server config. `is`
    # catches a re-introduced duplicate literal (these values aren't interned).
    assert hub.STALE_THRESHOLD is db.STALE_THRESHOLD
    assert hub.VISIBILITY_TIMEOUT is db.VISIBILITY_TIMEOUT
    assert hub.MESSAGE_TTL is db.MESSAGE_TTL

@pytest.mark.asyncio
async def test_api_recovery_middleware(test_client):
    # Cross-site POST to /api/reset -> 403
    res_reset = await test_client.post("/api/reset", headers={"Sec-Fetch-Site": "cross-site"})
    assert res_reset.status_code == 403

    # Cross-site POST to /api/restart -> 403 (Don't actually call a valid origin to avoid os._exit)
    res_restart = await test_client.post("/api/restart", headers={"Sec-Fetch-Site": "cross-site"})
    assert res_restart.status_code == 403
