import asyncio
import httpx
import json
import time
import sys

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

async def main():
    url = "http://localhost:8000/mcp/"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Origin": "http://localhost:8000"
    }

    print("Initializing connection to MCP Agent Hub...")
    # 1. Initialize Handshake
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "antigravity-cli", "version": "1.0"}
        }
    }
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=40) as client:
        res = await client.post(url, json=init_payload, headers=headers)
        if res.status_code not in (200, 202):
            print(f"Failed to initialize. Status: {res.status_code}")
            return

        session_id = res.headers.get("mcp-session-id")
        if not session_id:
            print("Session ID not found in response headers.")
            return
        
        headers["mcp-session-id"] = session_id
        print(f"Connected. Session ID: {session_id}")

        # 2. Send task message to wiki-forge
        payload_question = "Please fetch and synthesize what you know about A2A, ACP, and projects that connect two or more agents."
        send_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "send_message",
                "arguments": {
                    "sender_id": "antigravity-cli",
                    "recipient_id": "wiki-forge",
                    "payload": payload_question,
                    "subject": "Query: A2A, ACP, and Multi-Agent Hubs"
                }
            }
        }
        
        print("\nSending query message to wiki-forge...")
        res_send = await client.post(url, json=send_payload, headers=headers)
        send_data = parse_sse_response(res_send.text)
        print("DEBUG: send_data =", json.dumps(send_data, indent=2))
        
        # Parse the enqueued message info
        msg_info = None
        if "result" in send_data:
            result_obj = send_data["result"]
            if "structuredContent" in result_obj:
                sc = result_obj["structuredContent"]
                # Let's inspect the keys
                if "result" in sc:
                    msg_info = sc["result"]
                elif isinstance(sc, dict):
                    msg_info = sc
            elif "content" in result_obj:
                try:
                    # Let's see if the first content element is text and if we can parse it as JSON
                    text_content = result_obj["content"][0]["text"]
                    msg_info = json.loads(text_content)
                except Exception as e:
                    print("Failed parsing content text:", e)
        
        if not msg_info or "message_id" not in msg_info:
            print("Failed to send message or parse response.")
            return
            
        task_id = msg_info["message_id"]
        session_id = msg_info["session_id"]
        print(f"Query sent successfully! Message ID: {task_id}, Session ID: {session_id}")
        
        print("\nWaiting for wiki-forge to process the request and reply...")
        print("Polling inbox (kind='result') for reply. Press Ctrl+C to stop.")
        
        start_time = time.time()
        poll_count = 0
        while True:
            poll_count += 1
            # Call check_inbox with wait=True and a timeout
            inbox_payload = {
                "jsonrpc": "2.0",
                "id": 100 + poll_count,
                "method": "tools/call",
                "params": {
                    "name": "check_inbox",
                    "arguments": {
                        "agent_id": "antigravity-cli",
                        "wait": True,
                        "timeout": 15
                    }
                }
            }
            
            sys.stdout.write(f"\rPolling inbox (Attempt {poll_count}, elapsed: {int(time.time() - start_time)}s)... ")
            sys.stdout.flush()
            
            try:
                res_inbox = await client.post(url, json=inbox_payload, headers=headers)
                inbox_data = parse_sse_response(res_inbox.text)
                
                messages = []
                if "result" in inbox_data:
                    res_obj = inbox_data["result"]
                    if "structuredContent" in res_obj:
                        sc = res_obj["structuredContent"]
                        if "result" in sc:
                            messages = sc["result"]
                        elif isinstance(sc, list):
                            messages = sc
                    elif "content" in res_obj:
                        try:
                            messages = json.loads(res_obj["content"][0]["text"])
                        except:
                            pass
                            
                # Ensure messages is a list
                if isinstance(messages, dict):
                    messages = [messages]
                elif not isinstance(messages, list):
                    messages = []
                    
                for m in messages:
                    if m.get("kind") == "result" and m.get("parent_id") == task_id:
                        sys.stdout.write("Received result!\n")
                        sys.stdout.flush()
                        print("\n" + "="*80)
                        print(f"RESPONSE FROM WIKI-FORGE (Message: {task_id}):")
                        print("="*80)
                        print(m.get("response"))
                        print("="*80 + "\n")
                        return
                        
            except Exception as e:
                print(f"\nError checking inbox: {e}")
                
            await asyncio.sleep(2)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPolling stopped by user.")
