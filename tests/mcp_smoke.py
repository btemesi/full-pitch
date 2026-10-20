"""Manual smoke test: start the MCP server over stdio (clock frozen at 75'), list tools, call a few.

Run: python tests/mcp_smoke.py
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "fullpitch.mcp_server"],
                                   env={**os.environ, "FULLPITCH_LIVE_MINUTE": "75"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            for name, args in [("tactical_changes", {}), ("catch_up", {"minutes_away": 15}), ("key_moments", {})]:
                res = await s.call_tool(name, args)
                txt = res.content[0].text if res.content else str(res.structured_content)
                print(f"\n== {name} {args}\n", txt[:900])


asyncio.run(main())
