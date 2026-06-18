import asyncio
import httpx
import json

async def check():
    url = "http://localhost:8000/mcp/"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Origin": "http://localhost:8000"
    }

    # 1. Initialize Handshake
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "antigravity-cli-checker", "version": "1.0"}
        }
    }
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        res = await client.post(url, json=init_payload, headers=headers)
        print("Initialize status:", res.status_code)
        if res.status_code not in (200, 202):
            print(f"Failed to initialize. Status: {res.status_code}, Response: {res.text}")
            return

        session_id = res.headers.get("mcp-session-id")
        if not session_id:
            print("Session ID not found in headers")
            # Let's inspect all headers
            print("Headers:", dict(res.headers))
            return
        
        headers["mcp-session-id"] = session_id

        # 2. List Agents
        list_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_agents",
                "arguments": {}
            }
        }
        res_list = await client.post(url, json=list_payload, headers=headers)
        print("List status:", res_list.status_code)
        try:
            agents_result = res_list.json()
            print("--- Registered Agents ---")
            if "result" in agents_result:
                print(json.dumps(agents_result["result"], indent=2))
            else:
                print(agents_result)
        except Exception as e:
            print(f"Failed to parse agents JSON: {e}")
            print("Raw text:", res_list.text)

        # 3. Check Inbox for antigravity-cli
        inbox_payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "check_inbox",
                "arguments": {
                    "agent_id": "antigravity-cli",
                    "wait": False
                }
            }
        }
        res_inbox = await client.post(url, json=inbox_payload, headers=headers)
        print("\nInbox status:", res_inbox.status_code)
        try:
            inbox_result = res_inbox.json()
            print("--- Inbox for 'antigravity-cli' ---")
            if "result" in inbox_result:
                print(json.dumps(inbox_result["result"], indent=2))
            else:
                print(inbox_result)
        except Exception as e:
            print(f"Failed to parse inbox JSON: {e}")
            print("Raw text:", res_inbox.text)

if __name__ == "__main__":
    asyncio.run(check())
