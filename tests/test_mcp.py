"""MCP 工具测试：用进程内 Client 走完整 MCP 协议（序列化、参数校验、错误返回）。"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from conftest import build_retriever
from cooker.mcp_server import server
from cooker.service import CookerService


@pytest.fixture
def client(real_store, monkeypatch):
    svc = CookerService(real_store, build_retriever(real_store))
    monkeypatch.setattr(server, "get_service", lambda: svc)
    return Client(server.mcp)


async def test_lists_tools(client):
    async with client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"search_recipes", "get_recipe", "recommend_by_ingredients", "browse_recipes",
                     "scale_recipe", "make_shopping_list", "search_cooking_tips"}


async def test_get_recipe_and_scale(client):
    async with client:
        r = await client.call_tool("get_recipe", {"name": "番茄炒蛋"})
        assert r.structured_content["summary"]["name"] == "西红柿炒鸡蛋"
        s = await client.call_tool("scale_recipe", {"name": "西红柿炒鸡蛋", "people": 4})
        assert s.structured_content["people"] == 4 and s.structured_content["items"]


async def test_search_with_filter(client):
    async with client:
        r = await client.call_tool("search_recipes", {"query": "鸡翅", "categories": ["荤菜"], "limit": 3})
    results = r.structured_content["result"]
    assert results and all(x["recipe"]["category"] == "荤菜" for x in results)


async def test_shopping_list(client):
    async with client:
        r = await client.call_tool("make_shopping_list", {"dishes": [{"name": "西红柿炒鸡蛋", "people": 2},
                                                                      {"name": "可乐鸡翅", "people": 2}]})
    assert any(line["name"] == "鸡蛋" for line in r.structured_content["groceries"])


async def test_errors_are_readable(client):
    async with client:
        with pytest.raises(ToolError, match="没有找到菜谱"):
            await client.call_tool("get_recipe", {"name": "不存在的菜"})
        with pytest.raises(ToolError):  # 参数校验：人数超范围
            await client.call_tool("scale_recipe", {"name": "可乐鸡翅", "people": 0})
