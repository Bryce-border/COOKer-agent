"""以子进程方式启动 cooker-mcp（stdio），走真实 MCP 握手。"""
import asyncio, sys, time
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def main():
    t = StdioTransport(command=sys.argv[1], args=[], env={"PYTHONUTF8": "1"})
    t0 = time.perf_counter()
    async with Client(t) as c:
        tools = await c.list_tools()
        print(f"connected in {time.perf_counter()-t0:.1f}s, tools:", [x.name for x in tools])
        print("output schema present:", all(x.outputSchema for x in tools))
        r = await c.call_tool("get_recipe", {"name": "可乐鸡翅"})
        print("get_recipe:", r.structured_content["summary"]["name"], len(r.structured_content["steps"]), "step groups")
        r = await c.call_tool("scale_recipe", {"name": "可乐鸡翅", "people": 3})
        print("scale:", r.structured_content["factor_note"])
asyncio.run(main())
