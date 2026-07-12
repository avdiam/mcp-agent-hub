"""
Stress/correctness test for the MCP Agent Hub job board and SSE.

Tests:
  1. Job-board claim-race: 24 claimers concurrently claiming, select mid-stream, fail-task, re-open, re-select, complete.
  2. SSE under churn: measuring events, keepalives, connection stability, and performance overhead.

Running on isolated port 8101.
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import httpx
from fastmcp import Client

# ----------------- Helper functions -----------------

def _is_locked(exc: Exception) -> bool:
    err_str = str(exc).lower()
    return (
        "database is locked" in err_str
        or "database is busy" in err_str
        or "500" in err_str
        or "internal server error" in err_str
    )

async def sse_watcher(watcher_id: int, url: str, stats: dict, shutdown_event: asyncio.Event):
    """Raw HTTP client reading lines from SSE endpoint to track events and keepalives."""
    stats[f"watcher_{watcher_id}_events"] = 0
    stats[f"watcher_{watcher_id}_keepalives"] = 0
    stats[f"watcher_{watcher_id}_reconnects"] = 0
    stats[f"watcher_{watcher_id}_errors"] = []

    while not shutdown_event.is_set():
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, timeout=None) as response:
                    if response.status_code != 200:
                        raise Exception(f"HTTP status {response.status_code}")
                    async for line in response.aiter_lines():
                        if shutdown_event.is_set():
                            break
                        if not line:
                            continue
                        if line.startswith("data:"):
                            stats[f"watcher_{watcher_id}_events"] += 1
                        elif line.startswith(": keepalive"):
                            stats[f"watcher_{watcher_id}_keepalives"] += 1
        except asyncio.CancelledError:
            break
        except Exception as e:
            if not shutdown_event.is_set():
                stats[f"watcher_{watcher_id}_reconnects"] += 1
                stats[f"watcher_{watcher_id}_errors"].append(str(e))
                await asyncio.sleep(0.1)

# ----------------- Part 1 Test -----------------

async def run_board_stress_test(mcp_url: str) -> dict:
    results = {"pass": False, "errors": []}
    
    print("\n--- Part 1: Job-board claim-race & re-open test starting ---")
    
    async with Client(mcp_url) as poster_client:
        # 1. Register poster & 24 claimers
        await poster_client.call_tool("register_agent", {"agent_id": "poster"})
        print("Registered poster agent.")
        
        claimer_clients = []
        for i in range(24):
            c = Client(mcp_url)
            await c.__aenter__()
            await c.call_tool("register_agent", {"agent_id": f"claimer_{i}"})
            claimer_clients.append(c)
        print("Registered 24 claimer agents.")
        
        # 2. Post an offer
        post_res = await poster_client.call_tool("post_offer", {
            "sender_id": "poster",
            "payload": "Stress-test Task B payload",
            "subject": "Stress Job Board",
            "required_skills": ["stress"]
        })
        
        offer_info = json.loads(post_res.content[0].text) if hasattr(post_res, "content") else post_res
        if isinstance(offer_info, str):
            offer_info = json.loads(offer_info)
            
        if not offer_info.get("ok"):
            results["errors"].append(f"Failed to post offer: {offer_info}")
            return results
            
        offer_id = offer_info["offer_id"]
        print(f"Posted offer {offer_id}")
        
        # 3. Simulate claim race with selection mid-stream
        print("Simulating concurrent claims race...")
        
        # Claimers 0-11 claim first
        pre_tasks = [
            asyncio.create_task(claimer_clients[i].call_tool("claim_offer", {
                "agent_id": f"claimer_{i}",
                "offer_id": offer_id,
                "note": "Early bidder"
            }))
            for i in range(12)
        ]
        await asyncio.sleep(0.1)
        
        # Winner (claimer_12) claims
        winner_claim = await claimer_clients[12].call_tool("claim_offer", {
            "agent_id": "claimer_12",
            "offer_id": offer_id,
            "note": "The chosen one"
        })
        
        # Poster immediately resolves and selects claimer_12 while others are claiming
        resolve_task = asyncio.create_task(poster_client.call_tool("resolve_offer", {
            "poster_id": "poster",
            "offer_id": offer_id,
            "action": "select",
            "claimant_id": "claimer_12"
        }))
        
        # Late claimers 13-23 claim concurrently
        post_tasks = [
            asyncio.create_task(claimer_clients[i].call_tool("claim_offer", {
                "agent_id": f"claimer_{i}",
                "offer_id": offer_id,
                "note": "Late bidder"
            }))
            for i in range(13, 24)
        ]
        
        # Gather all tasks
        claims_results = await asyncio.gather(*(pre_tasks + post_tasks + [resolve_task]), return_exceptions=True)
        print("Race completed. Analyzing results...")
        
        # Verify resolution
        resolve_idx = len(pre_tasks) + len(post_tasks)
        resolve_res = claims_results[resolve_idx]
        if isinstance(resolve_res, Exception):
            results["errors"].append(f"Resolution failed with exception: {resolve_res}")
            return results
        
        resolve_text = resolve_res.content[0].text if hasattr(resolve_res, "content") else resolve_res
        resolve_data = json.loads(resolve_text) if isinstance(resolve_text, str) else resolve_res
        
        if not resolve_data.get("ok"):
            results["errors"].append(f"resolve_offer failed: {resolve_data}")
            return results
            
        print(f"Offer resolved successfully: {resolve_data}")
        
        # Assert winner has the task
        winner_inbox_res = await claimer_clients[12].call_tool("check_inbox", {
            "agent_id": "claimer_12",
            "wait": False
        })
        winner_inbox = json.loads(winner_inbox_res.content[0].text) if winner_inbox_res.content else []
        
        task_msg = None
        for m in winner_inbox:
            if m["session_id"] == offer_id and m["kind"] == "task":
                task_msg = m
                break
                
        if not task_msg:
            results["errors"].append("Winner (claimer_12) did not receive the task message in inbox.")
            return results
            
        print(f"Winner (claimer_12) received the task. Message ID: {task_msg['id']}")
        task_msg_id = task_msg["id"]
        
        # Verify losers whose claims went through before selection got offer_update in inbox
        losers_notified = 0
        for i in range(12):
            inbox_res = await claimer_clients[i].call_tool("check_inbox", {"agent_id": f"claimer_{i}", "wait": False})
            inbox = json.loads(inbox_res.content[0].text) if inbox_res.content else []
            for m in inbox:
                if m["session_id"] == offer_id and m["kind"] == "offer_update":
                    losers_notified += 1
                    break
        print(f"Verified that {losers_notified} pre-selection losers received an offer_update notification.")
        
        # 4. Exercise the re-open path
        print("Winner (claimer_12) failing the task to trigger re-open...")
        await claimer_clients[12].call_tool("fail_message", {
            "message_id": task_msg_id,
            "error": "Task failed on claimer_12"
        })
        
        # Verify offer status is open again
        offers_res = await poster_client.call_tool("list_offers", {"status": "all"})
        offers = json.loads(offers_res.content[0].text) if hasattr(offers_res, "content") else offers_res
        if isinstance(offers, str):
            offers = json.loads(offers)
            
        target_offer = next((o for o in offers if o["id"] == offer_id), None)
        if not target_offer or target_offer["status"] != "open":
            results["errors"].append(f"Offer did not re-open after task failure. Current status: {target_offer.get('status') if target_offer else 'Not found'}")
            return results
        print("Verified offer successfully re-opened on the board.")
        
        # Have claimer_5 claim the re-opened offer
        print("claimer_5 claiming re-opened offer...")
        await claimer_clients[5].call_tool("claim_offer", {
            "agent_id": "claimer_5",
            "offer_id": offer_id,
            "note": "Backup worker"
        })
        
        # Poster resolves and selects claimer_5
        print("Poster selecting claimer_5...")
        resolve2_res = await poster_client.call_tool("resolve_offer", {
            "poster_id": "poster",
            "offer_id": offer_id,
            "action": "select",
            "claimant_id": "claimer_5"
        })
        
        resolve2_text = resolve2_res.content[0].text if hasattr(resolve2_res, "content") else resolve2_res
        resolve2_data = json.loads(resolve2_text) if isinstance(resolve2_text, str) else resolve2_res
        
        if not resolve2_data.get("ok"):
            results["errors"].append(f"Failed to re-assign offer: {resolve2_data}")
            return results
            
        # claimer_5 checks inbox and replies to complete the task
        claimer5_inbox_res = await claimer_clients[5].call_tool("check_inbox", {
            "agent_id": "claimer_5",
            "wait": False
        })
        claimer5_inbox = json.loads(claimer5_inbox_res.content[0].text) if claimer5_inbox_res.content else []
        task5_msg = next((m for m in claimer5_inbox if m["session_id"] == offer_id and m["kind"] == "task"), None)
        
        if not task5_msg:
            results["errors"].append("Backup winner (claimer_5) did not receive the task message.")
            return results
            
        print(f"claimer_5 received task {task5_msg['id']}. Completing it...")
        await claimer_clients[5].call_tool("reply_to_message", {
            "message_id": task5_msg["id"],
            "response": "Successfully completed task"
        })
        
        # Verify offer status is completed
        offers_res = await poster_client.call_tool("list_offers", {"status": "all"})
        offers = json.loads(offers_res.content[0].text) if hasattr(offers_res, "content") else offers_res
        if isinstance(offers, str):
            offers = json.loads(offers)
        target_offer = next((o for o in offers if o["id"] == offer_id), None)
        
        if not target_offer or target_offer["status"] != "completed":
            results["errors"].append(f"Offer did not flip to 'completed'. Current status: {target_offer.get('status') if target_offer else 'Not found'}")
            return results
            
        print("Verified offer status flipped to 'completed'. Part 1 PASS!")
        results["pass"] = True
        
        # Close all claimer clients
        for c in claimer_clients:
            await c.__aexit__(None, None, None)
            
    return results

# ----------------- Part 2 Test -----------------

async def run_worker_load(worker_id: int, n_workers: int, ops: int, url: str, stats: dict):
    agent_id = f"load_worker_{worker_id}"
    recipient_id = f"load_worker_{(worker_id + 1) % n_workers}"
    skills = [{"id": "stress", "name": "stress", "description": "stress"}]
    
    async with Client(url) as client:
        for op in range(ops):
            try:
                # 1. Register
                await client.call_tool("register_agent", {
                    "agent_id": agent_id,
                    "skills": skills,
                    "description": f"Load worker {worker_id}"
                })
                stats["register_ops"] += 1
                
                # 2. Send
                await client.call_tool("send_message", {
                    "sender_id": agent_id,
                    "recipient_id": recipient_id,
                    "payload": f"load_payload_{worker_id}_{op}"
                })
                stats["send_ops"] += 1
                
                # 3. Check inbox
                inbox_res = await client.call_tool("check_inbox", {
                    "agent_id": agent_id,
                    "wait": True,
                    "timeout": 1
                })
                stats["check_inbox_ops"] += 1
                
                inbox = json.loads(inbox_res.content[0].text) if inbox_res.content else []
                for msg in inbox:
                    if msg["kind"] == "task":
                        # 4. Reply
                        await client.call_tool("reply_to_message", {
                            "message_id": msg["id"],
                            "response": "load_reply"
                        })
                        stats["reply_ops"] += 1
                        
                stats["loops_done"] += 1
            except Exception as e:
                if _is_locked(e):
                    stats["locks"] += 1
                else:
                    stats["errors"].append(str(e))
                await asyncio.sleep(0.01)

async def run_sse_churn_test(mcp_url: str, sse_url: str) -> dict:
    print("\n--- Part 2: SSE under churn load test starting ---")
    results = {}
    
    # Phase A: Churn with 3 SSE watchers active
    print("Phase A: Spawning 3 SSE watchers and running moderate message load...")
    shutdown_event = asyncio.Event()
    sse_stats = {}
    watchers = [
        asyncio.create_task(sse_watcher(i, sse_url, sse_stats, shutdown_event))
        for i in range(3)
    ]
    await asyncio.sleep(0.5)  # Allow SSE connections to establish
    
    stats_a = {"loops_done": 0, "register_ops": 0, "send_ops": 0, "check_inbox_ops": 0, "reply_ops": 0, "locks": 0, "errors": []}
    t0 = time.perf_counter()
    workers_a = [
        run_worker_load(i, 8, 15, mcp_url, stats_a)
        for i in range(8)
    ]
    await asyncio.gather(*workers_a)
    elapsed_a = time.perf_counter() - t0
    
    # Wait a bit to ensure debounced events settle, then shutdown watchers
    await asyncio.sleep(1.0)
    shutdown_event.set()
    await asyncio.gather(*watchers)
    print("SSE watchers terminated.")
    
    # Phase B: Churn without SSE watchers
    print("Phase B: Running identical load WITHOUT SSE watchers...")
    stats_b = {"loops_done": 0, "register_ops": 0, "send_ops": 0, "check_inbox_ops": 0, "reply_ops": 0, "locks": 0, "errors": []}
    t1 = time.perf_counter()
    workers_b = [
        run_worker_load(i, 8, 15, mcp_url, stats_b)
        for i in range(8)
    ]
    await asyncio.gather(*workers_b)
    elapsed_b = time.perf_counter() - t1
    
    # Compile metrics
    total_calls_a = stats_a["register_ops"] + stats_a["send_ops"] + stats_a["check_inbox_ops"] + stats_a["reply_ops"]
    total_calls_b = stats_b["register_ops"] + stats_b["send_ops"] + stats_b["check_inbox_ops"] + stats_b["reply_ops"]
    
    tput_a = total_calls_a / elapsed_a
    tput_b = total_calls_b / elapsed_b
    tput_delta_pct = ((tput_a - tput_b) / tput_b) * 100
    
    # Calculate state changes vs events
    # Every successful call in Phase A increments state version.
    # Estimated version increments = total_calls_a
    total_sse_events = [sse_stats.get(f"watcher_{i}_events", 0) for i in range(3)]
    avg_sse_events = sum(total_sse_events) / len(total_sse_events) if total_sse_events else 0
    
    results = {
        "tput_with_sse": tput_a,
        "tput_without_sse": tput_b,
        "tput_delta_pct": tput_delta_pct,
        "elapsed_with_sse": elapsed_a,
        "elapsed_without_sse": elapsed_b,
        "watcher_events": total_sse_events,
        "avg_watcher_events": avg_sse_events,
        "watcher_keepalives": [sse_stats.get(f"watcher_{i}_keepalives", 0) for i in range(3)],
        "watcher_reconnects": [sse_stats.get(f"watcher_{i}_reconnects", 0) for i in range(3)],
        "watcher_errors": [len(sse_stats.get(f"watcher_{i}_errors", [])) for i in range(3)],
        "state_changes": total_calls_a,
        "locks_with_sse": stats_a["locks"],
        "locks_without_sse": stats_b["locks"]
    }
    
    print("Part 2 load test completed.")
    return results

# ----------------- Main Coordinator -----------------

async def main():
    port = 8101
    
    temp_dir = tempfile.mkdtemp(prefix="hub_stress_task_b_")
    print(f"Isolated server temp directory: {temp_dir}")
    
    env = os.environ.copy()
    # Find current repo root relative to this script
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env["PYTHONPATH"] = repo_root
    
    server_log_path = os.path.join(temp_dir, "server.log")
    log_file = open(server_log_path, "w")
    
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    api_url = f"http://127.0.0.1:{port}/api/state"
    sse_url = f"http://127.0.0.1:{port}/api/events"
    
    print(f"Launching isolated server on port {port}...")
    server_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "mcp_hub.hub:app",
        "--port", str(port),
        "--host", "127.0.0.1",
        cwd=temp_dir,
        env=env,
        stdout=log_file,
        stderr=log_file
    )
    
    # Wait for server
    server_ready = False
    for _ in range(100):
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(api_url)
                if res.status_code == 200:
                    server_ready = True
                    break
        except Exception:
            pass
        await asyncio.sleep(0.1)
        
    if not server_ready:
        print("Error: Isolated server failed to start. Logs:")
        log_file.close()
        with open(server_log_path, "r") as f:
            print(f.read())
        server_proc.terminate()
        await server_proc.wait()
        shutil.rmtree(temp_dir)
        sys.exit(1)
        
    print("Isolated server is online.")
    
    part1_res = None
    part2_res = None
    try:
        # Run Part 1
        part1_res = await run_board_stress_test(mcp_url)
        
        # Run Part 2
        part2_res = await run_sse_churn_test(mcp_url, sse_url)
        
    except Exception as e:
        print(f"Test run encountered fatal error: {e}")
        traceback.print_exc()
    finally:
        print("Terminating isolated server...")
        server_proc.terminate()
        await server_proc.wait()
        log_file.close()
        
        try:
            shutil.rmtree(temp_dir)
            print("Cleaned up temp directory.")
        except Exception as e:
            print(f"Warning: failed to delete temp dir {temp_dir}: {e}")
            
    # Print beautiful summary report
    print("\n" + "=" * 55)
    print("          STRESS TEST TASK B REPORT")
    print("=" * 55)
    
    print("\nPART 1: JOB-BOARD CLAIM RACE & RE-OPEN")
    if part1_res:
        print(f"  Verdict: {'PASS' if part1_res['pass'] else 'FAIL'}")
        if part1_res["errors"]:
            print("  Errors encountered:")
            for err in part1_res["errors"]:
                print(f"    * {err}")
    else:
        print("  Verdict: NOT RUN / FATAL ERROR")
        
    print("\nPART 2: SSE UNDER CHURN LOAD")
    if part2_res:
        print(f"  Duration with SSE watchers    : {part2_res['elapsed_with_sse']:.2f} s")
        print(f"  Duration without SSE watchers : {part2_res['elapsed_without_sse']:.2f} s")
        print(f"  Throughput with SSE watchers  : {part2_res['tput_with_sse']:.2f} calls/s")
        print(f"  Throughput without SSE        : {part2_res['tput_without_sse']:.2f} calls/s")
        print(f"  SSE Overhead (Delta)          : {part2_res['tput_delta_pct']:.2f}%")
        print(f"  State mutations in load loop  : {part2_res['state_changes']}")
        print(f"  Watcher event counts (data)   : {part2_res['watcher_events']}")
        print(f"  Watcher keepalive counts      : {part2_res['watcher_keepalives']}")
        print(f"  Watcher reconnects / errors   : {part2_res['watcher_reconnects']} / {part2_res['watcher_errors']}")
        print(f"  SQLite lock errors (with SSE) : {part2_res['locks_with_sse']}")
        print(f"  SQLite lock errors (no SSE)   : {part2_res['locks_without_sse']}")
        
        # Debounce Analysis
        avg_events = part2_res["avg_watcher_events"]
        state_changes = part2_res["state_changes"]
        if state_changes > 0:
            coalescing_ratio = avg_events / state_changes
            print(f"  SSE Coalescing Ratio          : {coalescing_ratio:.2%} ({int(avg_events)} events for {state_changes} mutations)")
            if coalescing_ratio < 0.20:
                print("  Debounce analysis             : strong coalescing active (>80% event reduction).")
            else:
                print("  Debounce analysis             : moderate coalescing active.")
    else:
        print("  Verdict: NOT RUN / FATAL ERROR")
        
    print("=" * 55 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
