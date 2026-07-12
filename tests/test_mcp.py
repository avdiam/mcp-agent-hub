import json
import pytest
import pytest_asyncio
import asyncio
from httpx import ASGITransport, AsyncClient

from mcp_hub.hub import app, DB_PATH
from mcp_hub.hub import register_agent, send_message, check_inbox, reply_to_message, check_status, request_input, broadcast_message, list_agents, post_offer, claim_offer, resolve_offer, list_offers, Skill
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

    # Fresh StateNotifier per test (D38): its asyncio.Condition binds to the first
    # event loop that uses it, and pytest-asyncio gives every test its own loop.
    hub.notifier = hub.StateNotifier()

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
async def test_register_agent_skills_optional_and_preserved():
    # AHB-18: a bare register_agent(agent_id) must work — a required skills list drove
    # first-run CLI agents to skip registration — and a skill-less re-register must
    # refresh liveness without clobbering the previously advertised skills/description.
    await register_agent("newcomer")
    agents = {a["id"]: a for a in await list_agents()}
    assert json.loads(agents["newcomer"]["skills"]) == []

    await register_agent("veteran", [Skill(id="s1", name="Skill One", description="does things")], "a peer")
    await register_agent("veteran")
    agents = {a["id"]: a for a in await list_agents()}
    assert [s["id"] for s in json.loads(agents["veteran"]["skills"])] == ["s1"]
    assert agents["veteran"]["description"] == "a peer"

@pytest.mark.asyncio
async def test_register_agent_catches_up_missed_broadcasts():
    # AHB-1 P2 end-to-end: broadcast → late joiner registers → announcement lands in its
    # inbox via the normal check_inbox path; re-registering never re-delivers.
    await register_agent("announcer", [], "sender")
    await broadcast_message("announcer", "hub-wide news", subject="motd")

    res = await register_agent("late_joiner", [], "joined after the broadcast")
    assert "1 missed announcement" in res

    inbox = await check_inbox("late_joiner", wait=False)
    assert [m["kind"] for m in inbox] == ["announcement"]
    assert inbox[0]["payload"] == "hub-wide news"
    assert inbox[0]["subject"] == "motd"

    res2 = await register_agent("late_joiner", [], "re-register")
    assert "missed announcement" not in res2
    assert await check_inbox("late_joiner", wait=False) == []

@pytest.mark.asyncio
async def test_api_broadcast(test_client):
    # AHB-1 P2 dashboard control: POST /api/broadcast sends as "operator" through the same
    # capped db.broadcast path; a cap violation returns a clean 400.
    await register_agent("listener", [], "hears operator broadcasts")

    res = await test_client.post(
        "/api/broadcast",
        json={"payload": "maintenance at 18:00", "subject": "ops"},
        headers={"Origin": "http://localhost"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["delivered"] == 1  # only 'listener'; 'operator' isn't registered → no echo

    inbox = await check_inbox("listener", wait=False)
    assert len(inbox) == 1
    assert inbox[0]["kind"] == "announcement"
    assert inbox[0]["sender_id"] == "operator"

    # Immediate second operator broadcast trips the per-sender cooldown → clean 400
    res2 = await test_client.post(
        "/api/broadcast",
        json={"payload": "again too soon"},
        headers={"Origin": "http://localhost"},
    )
    assert res2.status_code == 400
    assert res2.json()["ok"] is False

@pytest.mark.asyncio
async def test_list_agents_status_is_liveness_derived(test_client):
    # AHB-15/D34: the MCP tool must not report the stored sticky 'online' for a
    # long-idle agent; list_agents and /api/state share one derivation and cannot diverge.
    import time
    import aiosqlite

    await register_agent("fresh_peer", [], "recently active")
    await register_agent("idle_peer", [], "long idle")

    async with aiosqlite.connect(hub.DB_PATH) as conn:
        await conn.execute(
            "UPDATE agents SET last_seen=? WHERE id=?",
            (time.time() - db.STALE_THRESHOLD - 60, "idle_peer"),
        )
        await conn.commit()

    tool_statuses = {a["id"]: a["status"] for a in await list_agents()}
    assert tool_statuses["fresh_peer"] == "online"
    assert tool_statuses["idle_peer"] == "stale"

    res = await test_client.get("/api/state", headers={"Origin": "http://localhost"})
    assert res.status_code == 200
    api_statuses = {a["id"]: a["status"] for a in res.json()["agents"]}
    assert api_statuses == tool_statuses

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
async def test_job_board_tool_roundtrip(test_client):
    # AHB-2 end-to-end at the tool layer: post → advert reaches peers → claims accumulate →
    # poster selects → winner gets a normal task → result fans back; /api/state shows the board.
    await register_agent("poster", [], "Poster")
    await register_agent("alice", [], "Alice")
    await register_agent("bob", [], "Bob")

    res = await post_offer("poster", "summarize the docs", subject="docs",
                           required_skills=["writing"])
    assert res["ok"] is True
    offer_id = res["offer_id"]

    alice_inbox = await check_inbox("alice", wait=False)
    assert [m["kind"] for m in alice_inbox] == ["announcement"]
    assert offer_id in alice_inbox[0]["payload"]

    board = await list_offers()
    assert [o["id"] for o in board] == [offer_id]
    assert board[0]["required_skills"] == ["writing"]

    assert (await claim_offer("alice", offer_id, note="I write well"))["ok"] is True
    assert (await claim_offer("bob", offer_id))["ok"] is True
    dup = await claim_offer("alice", offer_id)
    assert dup["ok"] is False

    sel = await resolve_offer("poster", offer_id, "select", claimant_id="alice")
    assert sel["ok"] is True
    task_id = sel["task_message_id"]

    # The board's default (open) view no longer lists it.
    assert await list_offers() == []
    assert (await list_offers(status="assigned"))[0]["claimant_id"] == "alice"

    # The winner works the assignment through the normal task machinery.
    alice_tasks = [m for m in await check_inbox("alice", wait=False) if m["kind"] == "task"]
    assert [m["id"] for m in alice_tasks] == [task_id]
    await reply_to_message(task_id, "summary attached")
    poster_results = [m for m in await check_inbox("poster", wait=False) if m["kind"] == "result"]
    assert len(poster_results) == 1
    assert poster_results[0]["response"] == "summary attached"
    assert poster_results[0]["session_id"] == offer_id

    # The loser was told, ack-lessly.
    bob_updates = [m for m in await check_inbox("bob", wait=False) if m["kind"] == "offer_update"]
    assert len(bob_updates) == 1
    assert "not selected" in bob_updates[0]["payload"]

    # Dashboard state carries the board; the fulfilled offer reads terminal 'completed'
    # (AHB-17 #3 — the task was completed above).
    res_state = await test_client.get("/api/state", headers={"Origin": "http://localhost"})
    assert res_state.status_code == 200
    offers = res_state.json()["offers"]
    assert offers[0]["id"] == offer_id
    assert offers[0]["status"] == "completed"

@pytest.mark.asyncio
async def test_post_offer_tool_cap_error():
    # A cap violation returns a clean structured error, not a raw exception.
    await register_agent("poster", [], "Poster")
    res = await post_offer("poster", "x" * 5000)
    assert res["ok"] is False
    assert "payload" in res["error"]
    assert await list_offers() == []

@pytest.mark.asyncio
async def test_api_recovery_middleware(test_client):
    # Cross-site POST to /api/reset -> 403
    res_reset = await test_client.post("/api/reset", headers={"Sec-Fetch-Site": "cross-site"})
    assert res_reset.status_code == 403

    # Cross-site POST to /api/restart -> 403 (Don't actually call a valid origin to avoid os._exit)
    res_restart = await test_client.post("/api/restart", headers={"Sec-Fetch-Site": "cross-site"})
    assert res_restart.status_code == 403

@pytest.mark.asyncio
async def test_security_headers_survive_asgi_middleware(test_client):
    # D38 rewrote OriginValidationMiddleware as pure ASGI — the D18 response headers
    # must still be injected on every response.
    res = await test_client.get("/api/state", headers={"Origin": "http://localhost"})
    assert res.status_code == 200
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors" in res.headers.get("content-security-policy", "")

@pytest_asyncio.fixture
async def live_server():
    # The SSE tests need a real HTTP server: httpx's ASGITransport runs the ASGI app
    # to completion and buffers the whole body, so an endless /api/events stream can
    # never be consumed through it. A real uvicorn on an ephemeral port also exercises
    # the stream through the pure-ASGI OriginValidationMiddleware (the exact
    # buffering interaction the AHB-14 watch-item flagged). lifespan="off" keeps the
    # background sweeper out of the test.
    import uvicorn

    config = uvicorn.Config(hub.app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(500):
        if server.started:
            break
        await asyncio.sleep(0.01)
    else:
        raise RuntimeError("uvicorn test server failed to start")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task

@pytest.mark.asyncio
async def test_api_events_snapshot_then_push(live_server, monkeypatch):
    # D38: /api/events opens with a full state snapshot, then pushes a fresh one
    # whenever hub state changes (here: an operator disconnect).
    monkeypatch.setattr(hub, "SSE_DEBOUNCE", 0.01)
    await db.upsert_agent(hub.DB_PATH, "watcher", "[]")

    async with AsyncClient() as rc:
        async with rc.stream("GET", f"{live_server}/api/events") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            lines = resp.aiter_lines()

            async def next_snapshot():
                async for line in lines:
                    if line.startswith("data: "):
                        return json.loads(line[len("data: "):])
                raise AssertionError("stream ended without a data event")

            snap = await asyncio.wait_for(next_snapshot(), timeout=5)
            assert set(snap) == {"agents", "messages", "offers", "events", "stats"}
            watcher = next(a for a in snap["agents"] if a["id"] == "watcher")
            assert watcher["status"] != "offline"

            reader = asyncio.create_task(next_snapshot())
            await asyncio.sleep(0.05)  # let the stream re-subscribe before the change
            res = await rc.post(f"{live_server}/api/agents/watcher/disconnect")
            assert res.status_code == 200
            snap2 = await asyncio.wait_for(reader, timeout=5)
            watcher2 = next(a for a in snap2["agents"] if a["id"] == "watcher")
            assert watcher2["status"] == "offline"

@pytest.mark.asyncio
async def test_api_events_keepalive_on_idle(live_server, monkeypatch):
    # D38: an idle stream stays alive via comment pings rather than data pushes.
    monkeypatch.setattr(hub, "SSE_KEEPALIVE", 0.05)
    monkeypatch.setattr(hub, "SSE_DEBOUNCE", 0.01)

    async with AsyncClient() as rc:
        async with rc.stream("GET", f"{live_server}/api/events") as resp:
            assert resp.status_code == 200

            async def scan_for_keepalive():
                got_snapshot = False
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        got_snapshot = True
                    elif line.startswith(": keepalive") and got_snapshot:
                        return True
                return False

            assert await asyncio.wait_for(scan_for_keepalive(), timeout=5)
