import asyncio
import httpx
import uvicorn
import multiprocessing
import time
import json

def run_server():
    uvicorn.run("hub:app", host="127.0.0.1", port=8000, log_level="error")

async def test():
    server_proc = multiprocessing.Process(target=run_server)
    server_proc.start()
    time.sleep(2) # wait for server to start

    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"}
                }
            }
            headers = {
                "Accept": "application/json, text/event-stream"
            }
            res = await client.post("http://127.0.0.1:8000/mcp/", json=payload, headers=headers)
            print("Status /mcp/:", res.status_code)
            print("Response /mcp/:", res.text)
            
            res2 = await client.post("http://127.0.0.1:8000/mcp", json=payload, headers=headers)
            print("Status /mcp:", res2.status_code)
            print("Response /mcp:", res2.text)

    finally:
        server_proc.terminate()
        server_proc.join()

if __name__ == "__main__":
    asyncio.run(test())

