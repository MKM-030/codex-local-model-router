"""Harmless stdio MCP fixture used only by the Codex integration test."""

import json, sys

for line in sys.stdin:
    try:
        req = json.loads(line)
        if "id" not in req:
            continue
        method = req.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": req.get("params", {}).get(
                    "protocolVersion", "2024-11-05"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bridge-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the bridge test string; has no side effects.",
                        "annotations": {
                            "readOnlyHint": True,
                            "destructiveHint": False,
                            "openWorldHint": False,
                        },
                        "inputSchema": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": "MCP_EXECUTED:"
                        + str(req["params"]["arguments"]["value"]),
                    }
                ]
            }
        elif method in ("resources/list", "resources/templates/list"):
            result = (
                {"resources": []}
                if method == "resources/list"
                else {"resourceTemplates": []}
            )
        else:
            result = {}
        print(
            json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}),
            flush=True,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req.get("id"),
                    "error": {"code": -32603, "message": type(exc).__name__},
                }
            ),
            flush=True,
        )
