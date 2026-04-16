"""Unity-specific validation helpers — MCP tool calls, availability check.

Extracted from validator.py to keep the main validator language-agnostic.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

_UNITY_MCP_URL = "http://127.0.0.1:27015/message"
_UNITY_MCP_HEALTH_URL = "http://127.0.0.1:27015/health"


def call_unity_mcp_tool(
    tool_name: str,
    arguments: dict,
    timeout: int = 10,
) -> Optional[dict]:
    """Call a Unity MCP tool via HTTP.  Returns the result dict or None on failure.

    Handles both plain JSON and SSE (text/event-stream) response formats.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        req = urllib.request.Request(
            _UNITY_MCP_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")

            if "text/event-stream" in content_type:
                for line in body.splitlines():
                    if line.startswith("data: "):
                        chunk = line[6:].strip()
                        if chunk in ("", "[DONE]"):
                            continue
                        parsed = json.loads(chunk)
                        if "result" in parsed:
                            return parsed["result"]
            else:
                parsed = json.loads(body)
                if "result" in parsed:
                    return parsed["result"]
    except Exception:
        return None
    return None


def unity_mcp_available() -> bool:
    """Return True if the Unity MCP server is reachable and healthy."""
    try:
        req = urllib.request.Request(_UNITY_MCP_HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False
