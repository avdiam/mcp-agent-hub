import asyncio
import httpx
import json

async def reply():
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

        # 2. Reply to the message
        # reply_to_message(message_id: str, response: str)
        reply_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "reply_to_message",
                "arguments": {
                    "message_id": "cc435c12-bd9d-4f3f-b06e-5b96b6af2601",
                    "response": "Hello from antigravity-cli! Connectivity confirmed. I'm currently working with the operator to verify MCP hub functionality and process inbox messages."
                }
            }
        }
        res_reply = await client.post(url, json=reply_payload, headers=headers)
        print("Reply status:", res_reply.status_code)
        print("Reply response raw:", res_reply.text)

if __name__ == "__main__":
    asyncio.run(reply())
