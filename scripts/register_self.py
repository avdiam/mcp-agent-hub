import asyncio
import httpx
import json

async def register():
    url = "http://localhost:8000/mcp"
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
            "clientInfo": {"name": "antigravity-cli-test", "version": "1.0"}
        }
    }
    
    async with httpx.AsyncClient() as client:
        # Check /mcp/ (FastMCP http_app redirects /mcp to /mcp/)
        res = await client.post(f"{url}/", json=init_payload, headers=headers)
        print("Initialize status:", res.status_code)
        print("Initialize headers:", res.headers)
        print("Initialize text:", res.text)
        if res.status_code != 200:
            return

        session_id = res.headers.get("mcp-session-id")
        if not session_id:
            print("Session ID not found in headers")
            return
        print(f"Acquired Session ID: {session_id}")
        
        # Include Session ID in headers
        headers["mcp-session-id"] = session_id

        # 2. Register Agent
        # register_agent(agent_id, skills, description)
        register_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "register_agent",
                "arguments": {
                    "agent_id": "antigravity-cli",
                    "skills": [
                        {
                            "id": "code-editor",
                            "name": "Code Editing",
                            "description": "Edits code files using precise replacement chunks",
                            "tags": ["code", "edit", "refactor"],
                            "examples": ["Refactor database.py to use connection pooling"]
                        },
                        {
                            "id": "file-search",
                            "name": "File Search",
                            "description": "Finds files or text patterns within the workspace",
                            "tags": ["search", "grep", "find"],
                            "examples": ["Search for TODO comments"]
                        }
                    ],
                    "description": "Antigravity CLI Agent - Pair programming assistant"
                }
            }
        }
        res_reg = await client.post(f"{url}/", json=register_payload, headers=headers)
        print("Register status:", res_reg.status_code)
        print("Register response:", res_reg.text)

        # 3. List Agents to confirm registration
        list_payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_agents",
                "arguments": {}
            }
        }
        res_list = await client.post(f"{url}/", json=list_payload, headers=headers)
        print("List status:", res_list.status_code)
        print("List response:", res_list.text)

if __name__ == "__main__":
    asyncio.run(register())
