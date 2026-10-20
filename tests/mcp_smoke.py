"""Smoke test: start the MCP server over stdio, list tools, call a few."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "fullpitch.mcp_server"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            for name, args in [("tactical_changes", {"at_minute": 50}),
                               ("tactical_changes", {"at_minute": 75}),
                               ("catch_up", {"from_minute": 55, "to_minute": 67, "at_minute": 67,
                                             "follow_player": "NOR-11"})]:
                res = await s.call_tool(name, args)
                txt = res.content[0].text if res.content else str(res.structured_content)
                print(f"\n== {name} {args}\n", txt[:1500])


asyncio.run(main())
