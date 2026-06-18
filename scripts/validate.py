import json
from mcp.types import JSONRPCMessage, ClientRequest

raw_message = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"}
    }
}

msg = JSONRPCMessage.model_validate(raw_message)
dumped = msg.root.model_dump(by_alias=True, mode="json", exclude_none=True)
print("Dumped:", json.dumps(dumped, indent=2))

try:
    req = ClientRequest.model_validate(dumped)
    print("ClientRequest passed:", req)
except Exception as e:
    print("Validation Error:", e)
