"""
HTTP/MCP-level stress / correctness harness for the MCP Agent Hub.

Spawns N concurrent fastmcp.Client connections to an isolated hub instance
running on port 8100, and hammers it via tool calls:
  register_agent -> send_message -> check_inbox(wait=True) -> reply_to_message

Usage:
  PYTHONPATH=. ./venv/Scripts/python.exe scripts/stress/http_loadtest.py --workers 24 --ops 50
"""
import argparse
import asyncio
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import httpx
from fastmcp import Client

def _is_locked(exc: Exception) -> bool:
    err_str = str(exc).lower()
    return (
        "database is locked" in err_str
        or "database is busy" in err_str
        or "500" in err_str
        or "internal server error" in err_str
    )

async def run_worker(worker_id: int, n_workers: int, ops_per_worker: int, url: str, stats: dict, latencies: list, claimed_msg_ids: set):
    agent_id = f"worker_{worker_id}"
    recipient_id = f"worker_{(worker_id + 1) % n_workers}"
    
    # Define a simple skill for registration
    skills = [
        {
            "id": "stress-test",
            "name": "Stress Test Tooling",
            "description": "Used to stress test the queue under load"
        }
    ]
    
    async with Client(url) as client:
        for op in range(ops_per_worker):
            try:
                # 1. Register agent (simulates write traffic on agent registry)
                await client.call_tool("register_agent", {
                    "agent_id": agent_id,
                    "skills": skills,
                    "description": f"Stress worker {worker_id}"
                })
                stats["register_ops"] += 1
                
                # 2. Send message to the next worker in the ring
                payload = f"msg_from_{agent_id}_op_{op}"
                send_res = await client.call_tool("send_message", {
                    "sender_id": agent_id,
                    "recipient_id": recipient_id,
                    "payload": payload
                })
                stats["send_ops"] += 1
                
                # 3. Check inbox (long-poll)
                inbox_res = await client.call_tool("check_inbox", {
                    "agent_id": agent_id,
                    "wait": True,
                    "timeout": 2
                })
                stats["check_inbox_ops"] += 1
                
                inbox = json.loads(inbox_res.content[0].text) if inbox_res.content else []
                for msg in inbox:
                    msg_id = msg["id"]
                    
                    # Track duplicate claims
                    if msg_id in claimed_msg_ids:
                        stats["double_deliveries"] += 1
                    else:
                        claimed_msg_ids.add(msg_id)
                    
                    # Track latency: claimed_at - created_at
                    # Both fields are floats (epoch seconds) in the DB response
                    created_at = msg.get("created_at")
                    claimed_at = msg.get("claimed_at")
                    if created_at and claimed_at:
                        latencies.append(claimed_at - created_at)
                    
                    # 4. Reply to the message
                    await client.call_tool("reply_to_message", {
                        "message_id": msg_id,
                        "response": f"ack_from_{agent_id}"
                    })
                    stats["reply_ops"] += 1
                    
                stats["ops_done"] += 1
                
            except Exception as e:
                if _is_locked(e):
                    stats["lock_errors"] += 1
                else:
                    stats["other_errors"].append(str(e))
                # Small sleep on error to avoid tight error looping
                await asyncio.sleep(0.01)

def get_percentile(data, p):
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=24, help="Number of concurrent workers")
    parser.add_argument("--ops", type=int, default=50, help="Operations per worker")
    parser.add_argument("--port", type=int, default=8100, help="Port to run the isolated server on")
    args = parser.parse_args()
    
    # 1. Setup isolated environment
    temp_dir = tempfile.mkdtemp(prefix="hub_stress_")
    print(f"Isolated server temp dir: {temp_dir}")
    
    env = os.environ.copy()
    # Ensure PYTHONPATH includes the current repo root
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env["PYTHONPATH"] = repo_root
    
    server_log_path = os.path.join(temp_dir, "server.log")
    log_file = open(server_log_path, "w")
    
    url = f"http://127.0.0.1:{args.port}/mcp"
    api_url = f"http://127.0.0.1:{args.port}/api/state"
    
    print(f"Launching isolated server at port {args.port}...")
    server_proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "mcp_hub.hub:app",
        "--port", str(args.port),
        "--host", "127.0.0.1",
        cwd=temp_dir,
        env=env,
        stdout=log_file,
        stderr=log_file
    )
    
    # Wait for server to start
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
        print("Error: Server failed to start. Content of server.log:")
        log_file.close()
        with open(server_log_path, "r") as f:
            print(f.read())
        server_proc.terminate()
        await server_proc.wait()
        shutil.rmtree(temp_dir)
        sys.exit(1)
        
    print("Isolated server is online.")
    
    # Initialize stats
    stats = {
        "ops_done": 0,
        "register_ops": 0,
        "send_ops": 0,
        "check_inbox_ops": 0,
        "reply_ops": 0,
        "lock_errors": 0,
        "double_deliveries": 0,
        "other_errors": []
    }
    latencies = []
    claimed_msg_ids = set()
    
    print(f"Starting load test: workers={args.workers}, ops_per_worker={args.ops}...")
    t0 = time.perf_counter()
    
    tasks = [
        run_worker(i, args.workers, args.ops, url, stats, latencies, claimed_msg_ids)
        for i in range(args.workers)
    ]
    await asyncio.gather(*tasks)
    
    elapsed = time.perf_counter() - t0
    print("Load test complete.")
    
    # Shutdown server
    print("Terminating server process...")
    server_proc.terminate()
    await server_proc.wait()
    log_file.close()
    
    # Cleanup temp directory
    try:
        shutil.rmtree(temp_dir)
    except Exception as e:
        print(f"Warning: Failed to cleanup temp dir {temp_dir}: {e}")
        
    # Print results
    total_mcp_calls = stats["register_ops"] + stats["send_ops"] + stats["check_inbox_ops"] + stats["reply_ops"]
    p50_lat = get_percentile(latencies, 0.50) * 1000
    p95_lat = get_percentile(latencies, 0.95) * 1000
    
    print("\n" + "=" * 40)
    print("           HTTP LOAD TEST RESULTS")
    print("=" * 40)
    print(f"Workers (concurrency) : {args.workers}")
    print(f"Ops per worker        : {args.ops}")
    print(f"Successful loops      : {stats['ops_done']}/{args.workers * args.ops}")
    print(f"Total MCP tool calls  : {total_mcp_calls}")
    print(f"Throughput (MCP call/s): {total_mcp_calls / elapsed:.2f}")
    print(f"Throughput (loops/s)  : {stats['ops_done'] / elapsed:.2f}")
    print(f"LOCK ERRORS (SQLite)  : {stats['lock_errors']}")
    print(f"DOUBLE DELIVERIES     : {stats['double_deliveries']}")
    print(f"Other errors          : {len(stats['other_errors'])}")
    if stats["other_errors"]:
        print(f"  First few: {stats['other_errors'][:3]}")
    print(f"Send->Deliver Latency :")
    print(f"  p50                 : {p50_lat:.1f} ms")
    print(f"  p95                 : {p95_lat:.1f} ms")
    print(f"Elapsed Time          : {elapsed:.2f} s")
    print("=" * 40 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
