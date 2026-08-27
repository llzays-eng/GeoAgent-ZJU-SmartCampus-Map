"""agent/tools/mcp_bridge.py — Python -> Node MCP bridge (outline section 11).

Everything else in this project is one Python process calling Python
functions. This module is deliberately different: it spawns the compiled
Node.js server in mcp-server/ as a subprocess and talks to it over the real
Model Context Protocol (stdio transport), using Anthropic's official `mcp`
Python SDK as the client. This is what "the Orchestrator calls a tool
through MCP instead of an in-process function call" concretely means — a
different tool-invocation transport, not just another Python function with
an MCP-flavored docstring.

Opt-in via GEOCODE_BACKEND=mcp (default is the in-process geocode tool in
agent/tools/geocode_tool.py, which has no subprocess/Node.js dependency and
is what runs unless explicitly switched). Requires:
  1. `pip install mcp` (see requirements.txt's commented-out mcp>=1.0.0 line)
  2. `cd mcp-server && npm install && npm run build`

Verified end-to-end in this project's own build process — see
mcp-server/test/test-client.mjs for the Node-side protocol test and this
file's __main__ block for the Python-side equivalent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MCP_SERVER_DIR = Path(__file__).resolve().parents[3] / "mcp-server"
MCP_SERVER_ENTRY = MCP_SERVER_DIR / "dist" / "index.js"


async def geocode_via_mcp(address: str) -> dict[str, Any]:
    """Spawns `node mcp-server/dist/index.js`, calls its
    `geocode_campus_location` tool over stdio, and returns the parsed
    result. Raises FileNotFoundError if the server hasn't been built yet
    (`npm run build`), and ImportError if the `mcp` package isn't installed
    — both are caught by agent/tools/geocode_tool.py's caller and treated
    as "MCP backend unavailable, use the in-process tool instead", the same
    graceful-degradation pattern used everywhere else in this project.
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise ImportError("`mcp` package not installed — run `pip install mcp` to use GEOCODE_BACKEND=mcp") from exc

    if not MCP_SERVER_ENTRY.exists():
        raise FileNotFoundError(
            f"MCP server not built at {MCP_SERVER_ENTRY} — run `cd mcp-server && npm install && npm run build`"
        )

    params = StdioServerParameters(command="node", args=[str(MCP_SERVER_ENTRY)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("geocode_campus_location", {"address": address})
            text = result.content[0].text if result.content else "{}"
            return json.loads(text)


if __name__ == "__main__":
    # Python-side equivalent of mcp-server/test/test-client.mjs — proves the
    # Python SDK can drive the real Node server end-to-end, not just that the
    # Node server works when tested from Node.
    import asyncio

    async def _smoke_test() -> None:
        for address in ["基础图书馆", "北教学区", "不存在的地名ABC"]:
            result = await geocode_via_mcp(address)
            status = f"ok, source={result.get('source')}, ({result.get('lat')}, {result.get('lon')})" if result.get("ok") else f"ok=false: {result.get('message')}"
            print(f'geocode_via_mcp("{address}") -> {status}')
        print("\n✓ Python -> Node MCP bridge working end-to-end")

    asyncio.run(_smoke_test())
