import asyncio
import httpx
import json

def parse_sse_response(text: str) -> dict:
    """Parses a Server-Sent Event formatted response to retrieve the JSON-RPC data."""
    for line in text.strip().split("\n"):
        if line.startswith("data:"):
            json_str = line[5:].strip()
            try:
                return json.loads(json_str)
            except Exception as e:
                print(f"Failed to parse JSON string: {json_str}. Error: {e}")
    return {}

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
        if res.status_code not in (200, 202):
            print(f"Failed to initialize. Status: {res.status_code}")
            return

        session_id = res.headers.get("mcp-session-id")
        if not session_id:
            print("Session ID not found")
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
        list_data = parse_sse_response(res_list.text)
        print("--- Registered Agents ---")
        if "result" in list_data and "structuredContent" in list_data["result"]:
            print(json.dumps(list_data["result"]["structuredContent"]["result"], indent=2))
        elif "result" in list_data and "content" in list_data["result"]:
            try:
                # Content text block might be JSON string
                raw_text = list_data["result"]["content"][0]["text"]
                print(json.dumps(json.loads(raw_text), indent=2))
            except:
                print(list_data["result"]["content"][0]["text"])
        else:
            print(list_data)

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
        inbox_data = parse_sse_response(res_inbox.text)
        print("\n--- Inbox for 'antigravity-cli' ---")
        if "result" in inbox_data and "structuredContent" in inbox_data["result"]:
            print(json.dumps(inbox_data["result"]["structuredContent"]["result"], indent=2))
        elif "result" in inbox_data and "content" in inbox_data["result"]:
            try:
                raw_text = inbox_data["result"]["content"][0]["text"]
                print(json.dumps(json.loads(raw_text), indent=2))
            except:
                print(inbox_data["result"]["content"][0]["text"])
        else:
            print(inbox_data)

if __name__ == "__main__":
    asyncio.run(check())
