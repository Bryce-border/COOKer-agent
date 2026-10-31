"""外部 MCP 服务连接器：通用的调用客户端、写操作确认 + 台账、工具生成。

新增一个服务 = 新建一个包，提供 connector.py（Connector 定义）+ tool_schemas.json + host_adapter.md，
再在 all_connectors() 里登记，并把技能放进 skills/。
"""

from __future__ import annotations

from functools import lru_cache

from cooker.connectors.base import Connector


@lru_cache
def all_connectors() -> tuple[Connector, ...]:
    # 延迟导入：不涉及外部服务的场景（入库、检索评测）不加载 MCP 依赖
    from cooker.luckin.connector import CONNECTOR as LUCKIN
    from cooker.mcd.connector import CONNECTOR as MCD

    conns = (MCD, LUCKIN)
    names = [n for c in conns for n in c.tool_names] + [t.name for c in conns for t in c.local_tools(c)]
    assert len(names) == len(set(names)), "不同连接器的工具重名，ToolNode 会无法区分"
    return conns


def by_key(key: str) -> Connector:
    return next(c for c in all_connectors() if c.key == key)


@lru_cache
def connector_tool_names() -> frozenset[str]:
    """全部外部服务工具名（含宿主本地工具）。"""
    local = [t.name for c in all_connectors() for t in c.local_tools(c)]
    return frozenset([*(n for c in all_connectors() for n in c.tool_names), *local])
