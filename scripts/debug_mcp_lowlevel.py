import asyncio
from mcp.server.lowlevel.server import Server
from mcp.types import InitializeRequestParams, ClientCapabilities, Implementation

async def test():
    s = Server("test")
    # Actually, fastmcp server is FastMCP("test")._mcp_server
    
    from fastmcp import FastMCP
    app = FastMCP("test")
    
    server = app._mcp_server
    
    try:
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        # simulate
        print(server)
    except Exception as e:
        print(e)

asyncio.run(test())
