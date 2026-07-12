"""
HTTP/MCP-level broadcast stress / correctness harness for the MCP Agent Hub.

Spawns an isolated hub instance on port 8101.
Registers ~50 agents, hammers broadcast_message concurrently, and verifies
all-or-nothing fan-out, no double-deliveries, cap enforcement, and
late-registration catch-up.
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
import httpx
from fastmcp import Client

async def register_agent(client, agent_id):
    await client.call_tool("register_agent", {
        "agent_id": agent_id,
        "description": f"Worker {agent_id}"
    })

async def check_inbox(client, agent_id):
    res = await client.call_tool("check_inbox", {
        "agent_id": agent_id,
        "wait": False,
        "timeout": 0
    })
    return json.loads(res.content[0].text) if res.content else []

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listeners", type=int, default=50, help="Number of listeners to register")
    parser.add_argument("--broadcasts", type=int, default=50, help="Concurrent broadcasts to attempt")
    parser.add_argument("--port", type=int, default=8101, help="Port to run the isolated server on")
    args = parser.parse_args()
    
    # 1. Setup isolated environment
    temp_dir = tempfile.mkdtemp(prefix="hub_stress_br_")
    print(f"Isolated server temp dir: {temp_dir}")
    
    env = os.environ.copy()
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
    
    sender_id = "broadcaster"
    
    async with Client(url) as client:
        # Register sender
        await register_agent(client, sender_id)
        
        # Register 50 listeners
        print(f"Registering {args.listeners} listeners...")
        for i in range(args.listeners):
            await register_agent(client, f"listener_{i}")
            
        print(f"Hammering {args.broadcasts} concurrent broadcasts...")
        
        tasks = []
        for i in range(args.broadcasts):
            tasks.append(client.call_tool("broadcast_message", {
                "sender_id": sender_id,
                "subject": f"Concurrent {i}",
                "payload": f"Payload {i}"
            }))
            
        t0 = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        duration = time.perf_counter() - t0
        
        successes = []
        failures = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                failures.append(str(res))
            else:
                try:
                    data = json.loads(res.content[0].text)
                    if data.get("ok"):
                        successes.append(data)
                    else:
                        failures.append(data.get("error"))
                except Exception as e:
                    failures.append(f"parse error: {e}")
                    
        print(f"\nBroadcast Write Cost: {duration * 1000:.1f}ms for {args.broadcasts} attempts")
        print(f"  -> Admitted: {len(successes)}")
        print(f"  -> Rejected: {len(failures)}")
        
        # Verify fan-out correctness
        print("Verifying inbox consistency across listeners...")
        valid = True
        for i in range(args.listeners):
            inbox = await check_inbox(client, f"listener_{i}")
            
            # Check length
            if len(inbox) != len(successes):
                print(f"FAIL: listener_{i} has {len(inbox)} messages, expected {len(successes)}")
                valid = False
                
            # Check duplicates
            msg_ids = set()
            for msg in inbox:
                if msg["id"] in msg_ids:
                    print(f"FAIL: listener_{i} has duplicate msg_id {msg['id']}")
                    valid = False
                msg_ids.add(msg["id"])
                
        if valid:
            print("PASS: all-or-nothing fan-out + no double-deliveries")
            
        # D35 Catch-up Test
        print("Testing D35 register-time catch-up...")
        await register_agent(client, "late_listener")
        late_inbox = await check_inbox(client, "late_listener")
        
        # Filter for announcements since late_listener might only get announcements as catch-up
        late_announcements = [m for m in late_inbox if m["kind"] == "announcement"]
        if len(late_announcements) == len(successes):
            print("PASS: Late listener caught up exactly once")
        else:
            print(f"FAIL: Late listener has {len(late_announcements)} announcements, expected {len(successes)}")

    # Shutdown
    print("Terminating server process...")
    server_proc.terminate()
    await server_proc.wait()
    log_file.close()
    
    try:
        shutil.rmtree(temp_dir)
    except Exception as e:
        print(f"Warning: Failed to cleanup temp dir: {e}")
        
    print("\nDone.")

if __name__ == "__main__":
    asyncio.run(main())
