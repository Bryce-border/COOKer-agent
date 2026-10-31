"""从官方 MCP 拉取工具清单与入参结构，写入 src/cooker/<服务>/tool_schemas.json。

只调用 list_tools（协议层的「列出工具」），不调用任何业务工具，不会查询或改动账户。
快照用于离线绑定工具；每次真实调用时客户端仍会用线上结构再校验一次参数（见 connectors/client.py）。

    .venv/Scripts/python.exe scripts/sync_mcp_tools.py mcd
    .venv/Scripts/python.exe scripts/sync_mcp_tools.py luckin
"""

import asyncio
import json
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from cooker.connectors import by_key
from cooker.connectors.client import schema_of

# JSON Schema 之外的私有字段（瑞幸返回 returnDirect）：模型接口可能拒收，快照里去掉
NON_STANDARD_KEYS = ("returnDirect",)


async def main(key: str) -> None:
    conn = by_key(key)
    creds = conn.credentials()
    async with Client(StreamableHttpTransport(creds.url, auth=creds.token), timeout=25) as client:
        tools = await client.list_tools()
    snapshot = []
    for t in sorted(tools, key=lambda t: t.name):
        schema = {k: v for k, v in schema_of(t).items() if k not in NON_STANDARD_KEYS}
        snapshot.append({"name": t.name, "description": t.description or "", "input_schema": schema})
    text = json.dumps(snapshot, ensure_ascii=False, indent=1)
    assert creds.token not in text
    path = conn.package_dir / "tool_schemas.json"
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{conn.label}：{len(snapshot)} 个工具 → {path}")
    names = {t["name"] for t in snapshot}
    # 新工具不会自动暴露给模型：先判断它有没有副作用，再加入 connector.py 的 READ_TOOLS 或 WRITE_TOOLS
    if new := sorted(names - conn.tool_names):
        print("未分类（不会绑定给模型）：", new)
    if gone := sorted(conn.tool_names - names):
        print("白名单中但服务端已没有：", gone)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "mcd"))
