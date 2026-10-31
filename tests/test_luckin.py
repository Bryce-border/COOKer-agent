"""瑞幸（my-coffee 技能）接入的离线测试。

假服务：内存中的 FastMCP 实例，走真实 MCP 协议；返回结构按 2026-10-31 实测的真实返回构造（门店、预览字段、金额单位为元）。
不连瑞幸服务，不会产生订单。
"""

import asyncio
import copy
import json

import pytest
from fastmcp import FastMCP
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from cooker.agent import graph as graph_mod
from cooker.agent import verify as verify_mod
from cooker.agent.tools import Context
from cooker.connectors import actions, client
from cooker.connectors.base import Credentials
from cooker.connectors.ledger import Ledger
from cooker.luckin.connector import CONNECTOR as LUCKIN
from cooker.mcd.connector import CONNECTOR as MCD
from cooker.skills import active_tools
from test_agent import ScriptedModel, call

TOKEN = "luckin-test-token"
CFG = {"configurable": {"thread_id": "lk1"}}
SHOP = {"deptId": 390217, "deptName": "上海来福士广场店", "address": "黄浦区西藏中路268号上海来福士广场负一层12B室",
        "longitude": 121.476095, "latitude": 31.232526, "workTimeStart": "07:30", "workTimeEnd": "21:30",
        "workStatus": "营业中"}
LINE = {"amount": 1, "productId": 1262, "skuCode": "SP2077-01134"}
PIC = "https://img06.luckincoffeecdn.com/group5/M00/B2/CF/demo.png"
PREVIEW = {"code": 0, "msg": "success", "success": True, "data": {
    "discountPrice": 16.0, "privilegeMoney": 4.0, "totalInitialPrice": 20.0, "couponCodeList": ["CPN-1"],
    "shopInfo": SHOP,
    "productInfoList": [{**LINE, "name": "生椰拿铁（原创）", "breviaryPicUrl": PIC,
                         "additionDesc": "大杯/冰/意式拼配/默认浓度/不另外加糖/无奶油"}]}}
CREATED = {"code": 0, "msg": "success", "success": True, "data": {
    "orderId": "LK123", "discountPrice": 16.0,
    "payOrderQrCodeUrl": "https://pay.example/qr/LK123.png", "payOrderUrl": "https://pay.example/h5/LK123"}}
# 下单参数：模型给的是用户所在地的粗略坐标、没传券码
ORDER_ARGS = {"deptId": 390217, "productList": [LINE], "longitude": 121.47, "latitude": 31.23}


class FakeLuckin:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.preview = copy.deepcopy(PREVIEW)

    def server(self) -> FastMCP:
        mcp = FastMCP("fake-luckin")

        @mcp.tool(name="queryShopList")
        def shops(longitude: float, latitude: float, deptName: str | None = None) -> dict:
            self.calls.append(("queryShopList", {"longitude": longitude}))
            return {"code": 0, "msg": "success", "success": True, "data": [SHOP]}

        @mcp.tool(name="searchProductForMcp")
        def search(deptId: int, query: str) -> dict:
            self.calls.append(("searchProductForMcp", {"query": query}))
            return {"code": 0, "success": True, "data": [
                {**LINE, "productName": "生椰拿铁（原创）", "pictureUrl": PIC, "estimatePrice": 20.0},
                {"productId": 9, "skuCode": "X", "productName": "外链商品", "pictureUrl": "https://tracker.example/p.gif"}]}

        @mcp.tool(name="previewOrder")
        def preview(deptId: int, productList: list[dict]) -> dict:
            self.calls.append(("previewOrder", {"deptId": deptId}))
            return self.preview

        @mcp.tool(name="createOrder")
        def create(deptId: int, productList: list[dict], longitude: float, latitude: float,
                   couponCodeList: list[str] | None = None, remark: str | None = None) -> dict:
            self.calls.append(("createOrder", {"longitude": longitude, "latitude": latitude,
                                               "couponCodeList": couponCodeList}))
            return CREATED

        @mcp.tool(name="queryOrderDetailInfo")
        def detail(orderId: str) -> dict:
            self.calls.append(("queryOrderDetailInfo", {"orderId": orderId}))
            return {"code": 0, "success": True, "data": {"orderId": orderId, "orderStatusName": "待支付",
                                                          "deptName": SHOP["deptName"], "discountPrice": 16.0}}

        @mcp.tool(name="cancelOrder")
        def cancel(orderId: str) -> dict:
            self.calls.append(("cancelOrder", {"orderId": orderId}))
            return {"code": 0, "success": True, "data": True}

        return mcp

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeLuckin()
    monkeypatch.setattr(client, "open_transport", lambda conn, creds: f.server())
    monkeypatch.setattr(client, "load_credentials", lambda conn: Credentials(conn.url, TOKEN))
    monkeypatch.setattr(actions, "Ledger", lambda: Ledger(tmp_path / "ledger.sqlite3"))
    monkeypatch.setattr(verify_mod, "_library", lambda: ({}, ()))
    return f


@pytest.fixture
def make_graph(fake, monkeypatch):
    def make(script):
        model = ScriptedModel(script=list(script), seen=[])
        model.skills = []

        def get_model(provider=None, require_tool=False, skills=()):
            model.skills.append(skills)
            return model

        monkeypatch.setattr(graph_mod, "get_agent_model", get_model)
        return graph_mod.build_graph(checkpointer=InMemorySaver(), store=InMemoryStore()), model

    return make


def run(g, payload):
    payload = payload if isinstance(payload, Command) else {"messages": [HumanMessage(payload)]}
    return g.invoke(payload, CFG, context=Context())


def last_tool(state) -> str:
    return [m for m in state["messages"] if isinstance(m, ToolMessage)][-1].content


LOAD = call("load_skill", {"name": "my-coffee"}, "load")


def test_loading_coffee_skill_binds_only_luckin_tools(make_graph):
    g, model = make_graph([LOAD, AIMessage("好的，你在哪附近？")])
    state = run(g, "帮我点一杯生椰拿铁")
    assert state["skills"] == ["my-coffee"] and model.skills[-1] == ("my-coffee",)
    names = {t.name for t in active_tools(["my-coffee"])}
    assert names == LUCKIN.tool_names and not names & MCD.tool_names
    prompt = next(m.content for m in model.seen[-1] if isinstance(m, SystemMessage))
    assert "瑞幸咖啡下单助手" in prompt and "COOKer 宿主适配" in prompt and "七件事" not in prompt


def test_snapshot_has_no_private_schema_keys():
    assert all("returnDirect" not in s["input_schema"] for s in LUCKIN.schemas.values())


def test_read_tool_goes_through_mcp(make_graph, fake):
    g, _ = make_graph([LOAD, call("queryShopList", {"longitude": 121.47, "latitude": 31.23}, "s1"), AIMessage("…")])
    assert "上海来福士广场店" in last_tool(run(g, "附近有瑞幸吗"))
    assert fake.names() == ["queryShopList"]


def order_card(make_graph, extra=()):
    g, _ = make_graph([LOAD, call("createOrder", ORDER_ARGS, "o1"), *extra])
    run(g, "就这家，下单")
    (intr,) = g.get_state(CFG).interrupts
    return g, intr.value


def test_order_card_comes_from_live_preview(make_graph, fake):
    _, card = order_card(make_graph)
    rows = dict(map(tuple, card["rows"]))
    assert card["connector"] == "luckin" and card["title"] == "确认瑞幸下单"
    assert rows["应付"] == "¥16.00" and rows["减免"] == "¥4.00" and rows["优惠券"] == "自动使用 1 张"
    assert "大杯/冰" in rows["商品"] and "上海来福士广场店" in rows["门店"]
    assert fake.names() == ["previewOrder"]  # 只做了预览，没有建单


def test_confirm_passes_coupons_and_store_coordinates(make_graph, fake):
    g, card = order_card(make_graph, [AIMessage("请扫码支付")])
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    result = json.loads(last_tool(state))
    (created,) = [a for n, a in fake.calls if n == "createOrder"]
    assert created["couponCodeList"] == ["CPN-1"]  # 模型没传，代码按预览透传
    assert (created["longitude"], created["latitude"]) == (SHOP["longitude"], SHOP["latitude"])  # 门店坐标
    assert result["status"] == "SUCCESS" and result["result"]["payOrderQrCodeUrl"].endswith("LK123.png")
    assert "payOrderUrl" not in last_tool(state)  # 技能禁止展示的字段已移除


def test_mismatched_preview_is_refused(make_graph, fake):
    fake.preview["data"]["productInfoList"][0]["skuCode"] = "OTHER-SKU"
    g, _ = make_graph([LOAD, call("createOrder", ORDER_ARGS, "o1"), AIMessage("明细不一致")])
    state = run(g, "下单")
    assert json.loads(last_tool(state))["status"] == "REFUSED" and not g.get_state(CFG).interrupts
    assert "createOrder" not in fake.names()


def test_closed_store_is_warned(make_graph, fake):
    fake.preview["data"]["shopInfo"] = {**SHOP, "workStatus": "打烊中"}
    _, card = order_card(make_graph)
    assert any("打烊中" in w for w in card["warnings"])


def test_price_change_during_confirmation_blocks_order(make_graph, fake):
    g, card = order_card(make_graph, [AIMessage("价格变了")])
    fake.preview["data"]["discountPrice"] = 18.0
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    assert json.loads(last_tool(state))["status"] == "CHANGED" and "createOrder" not in fake.names()


def test_cancel_checks_order_first_then_runs_once(make_graph, fake):
    g, _ = make_graph([LOAD, call("cancelOrder", {"orderId": "LK123"}, "c1"), AIMessage("已取消")])
    run(g, "不要了，取消订单 LK123")
    card = g.get_state(CFG).interrupts[0].value
    assert dict(map(tuple, card["rows"]))["当前状态"] == "待支付" and fake.names() == ["queryOrderDetailInfo"]
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    assert json.loads(last_tool(state))["status"] == "SUCCESS" and fake.names().count("cancelOrder") == 1


def test_freeze_is_per_connector(fake, tmp_path):
    """瑞幸一笔结果未知只冻结瑞幸，不影响麦当劳（台账按「服务 + Token 摘要」隔离）。"""
    ledger = Ledger(tmp_path / "ledger.sqlite3")
    creds = Credentials(LUCKIN.url, TOKEN)
    ledger.begin("x1", client.account_key(LUCKIN, creds), "createOrder", {}, "fp")  # 停在 IN_FLIGHT
    frozen = json.loads(asyncio.run(actions.run_write(LUCKIN, "cancelOrder", {"orderId": "LK1"}, "x2")))
    assert frozen["status"] == "FROZEN"
    assert ledger.pending(client.account_key(MCD, Credentials(MCD.url, TOKEN))) is None


def test_product_images_go_to_artifact_not_to_model(make_graph, fake):
    g, model = make_graph([LOAD, call("searchProductForMcp", {"deptId": 390217, "query": "生椰拿铁"}, "p1"),
                           AIMessage("有**生椰拿铁（原创）**")])
    state = run(g, "来福士店有生椰拿铁吗")
    msg = [m for m in state["messages"] if isinstance(m, ToolMessage)][-1]
    assert msg.artifact == {"images": [{"name": "生椰拿铁（原创）", "url": PIC}]}  # 外链商品被白名单挡掉
    # 发给模型的就是 ToolMessage.content；artifact 不会被转成模型输入
    from langchain_core.messages import convert_to_openai_messages
    sent = convert_to_openai_messages(model.seen[-1])
    assert "tracker.example" not in json.dumps(sent) and all("artifact" not in m for m in sent)


def test_order_card_shows_product_thumbnail(make_graph, fake):
    _, card = order_card(make_graph)
    assert card["images"] == [{"name": "生椰拿铁（原创）", "url": PIC}]
