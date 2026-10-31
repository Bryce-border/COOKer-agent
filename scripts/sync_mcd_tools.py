"""从麦当劳官方 MCP 拉取工具清单与入参结构，写入 src/cooker/mcd/tool_schemas.json。

只调用 list_tools（协议层的「列出工具」），不调用任何业务工具，不会查询或改动账户。
快照用于离线绑定工具；每次真实调用时客户端仍会用线上结构再校验一次参数（见 client.py）。

    .venv/Scripts/python.exe scripts/sync_mcd_tools.py
"""

import asyncio
import json

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from cooker.mcd.client import READ_TOOLS, SCHEMA_PATH, WRITE_TOOLS, _schema_of
from cooker.mcd.config import McdSettings


async def main() -> None:
    settings = McdSettings()
    token = settings.token.get_secret_value()
    if not token:
        raise SystemExit("未配置 MCD_MCP_TOKEN（config/mcd-mcp.local.env）")
    async with Client(StreamableHttpTransport(settings.url, auth=token), timeout=25) as client:
        tools = await client.list_tools()
    snapshot = [{"name": t.name, "description": t.description or "", "input_schema": _schema_of(t)}
                for t in sorted(tools, key=lambda t: t.name)]
    text = json.dumps(snapshot, ensure_ascii=False, indent=1)
    assert token not in text
    SCHEMA_PATH.write_text(text + "\n", encoding="utf-8")
    print(f"{len(snapshot)} 个工具 → {SCHEMA_PATH}")
    names = {t["name"] for t in snapshot}
    # 新工具不会自动暴露给模型：先判断它有没有副作用，再加入 client.py 的 READ_TOOLS 或 WRITE_TOOLS
    if new := sorted(names - READ_TOOLS - WRITE_TOOLS):
        print("未分类（不会绑定给模型）：", new)
    if gone := sorted((READ_TOOLS | WRITE_TOOLS) - names):
        print("白名单中但服务端已没有：", gone)


if __name__ == "__main__":
    asyncio.run(main())
