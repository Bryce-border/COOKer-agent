"""麦当劳技能接入的离线测试。

假服务：内存中的 FastMCP 实例，走真实 MCP 协议（initialize → list_tools → call_tool），数据来自
tests/fixtures/mcd（取自 mclottery-alchemist 的真实结构样例）。不连麦当劳服务，不会产生任何扣费。
"""

import asyncio
import copy
import json
import time
from pathlib import Path

import pytest
from fastmcp import FastMCP
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from cooker.agent import graph as graph_mod
from cooker.agent import verify as verify_mod
from cooker.agent.tools import Context
from cooker.mcd import actions, client
from cooker.mcd.config import McdSettings
from cooker.mcd.ledger import Ledger
from cooker.skills import available, read_skill_file
from test_agent import ScriptedModel, call

FIX = Path(__file__).parent / "fixtures" / "mcd"
SKILL = "mclottery-alchemist"
TOKEN = "test-token-123"
CFG = {"configurable": {"thread_id": "m1"}}


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class FakeMcd:
    """记录每次业务调用；可以改返回、制造超时。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.lottery = fixture("lottery_running_ok")
        self.draw_delay = 0.0
        self.account = fixture("account_normal")

    def server(self) -> FastMCP:
        mcp = FastMCP("fake-mcd")

        def log(name, **args):
            self.calls.append((name, {k: v for k, v in args.items() if v is not None}))

        @mcp.tool(name="query-my-account")
        def account() -> dict:
            log("query-my-account")
            return self.account

        @mcp.tool(name="query-lottery-info")
        def lottery() -> dict:
            log("query-lottery-info")
            return self.lottery

        @mcp.tool(name="draw-lottery")
        def draw() -> dict:
            log("draw-lottery")
            time.sleep(self.draw_delay)
            return fixture("draw_win")

        @mcp.tool(name="auto-bind-coupons")
        def bind() -> str:
            log("auto-bind-coupons")
            return "总计 1 张，成功 1 张"

        @mcp.tool(name="calculate-price")
        def price(storeCode: str, orderType: int, beType: int, items: list[dict], beCode: str | None = None) -> dict:
            log("calculate-price", storeCode=storeCode, items=items)
            return fixture("price_result")

        @mcp.tool(name="create-order")
        def create(storeCode: str, orderType: int, beType: int, items: list[dict], takeWayCode: str | None = None,
                   addressId: str | None = None, beCode: str | None = None) -> dict:
            log("create-order", storeCode=storeCode, takeWayCode=takeWayCode)
            return fixture("order_created")

        @mcp.tool(name="query-order")
        def order(orderId: str) -> dict:
            log("query-order", orderId=orderId)
            return {"success": True, "code": 200, "data": {"orderDetail": {"orderStatus": "待支付"}}, "token": TOKEN}

        return mcp

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeMcd()
    monkeypatch.setattr(client, "open_transport", lambda settings: f.server())
    monkeypatch.setattr(client, "load_settings", lambda: McdSettings(token=TOKEN))
    monkeypatch.setattr(actions, "Ledger", lambda: Ledger(tmp_path / "ledger.sqlite3"))
    monkeypatch.setattr(verify_mod, "_library", lambda: ({}, ()))  # 本文件不测菜名校验
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


def last_tool(state) -> str:
    return [m for m in state["messages"] if isinstance(m, ToolMessage)][-1].content


def system_prompt(model, i) -> str:
    return next(m.content for m in model.seen[i] if isinstance(m, SystemMessage))


def run(g, text_or_cmd):
    payload = text_or_cmd if isinstance(text_or_cmd, Command) else {"messages": [HumanMessage(text_or_cmd)]}
    return g.invoke(payload, CFG, context=Context())


LOAD = call("load_skill", {"name": SKILL}, "load")


# ---------- 技能加载（渐进式披露） ----------


def test_skill_is_discovered_from_skill_md():
    skill = available()[SKILL]
    assert "麦当劳" in skill.description
    assert "references/ordering-workflow.md" in skill.files()
    assert len(skill.tools) == 36 and "COOKer 宿主适配" in skill.adapter


def test_read_skill_file_only_allows_listed_references():
    assert "点餐" in read_skill_file.invoke({"name": SKILL, "path": "references/ordering-workflow.md"})
    for bad in ["../SKILL.md", "references/../../../.env", "SKILL.md", "LICENSE"]:
        assert read_skill_file.invoke({"name": SKILL, "path": bad}).startswith("没有该文件")


def test_cooking_turn_keeps_skill_unloaded(make_graph):
    g, model = make_graph([AIMessage("红烧肉要先焯水")])
    run(g, "红烧肉怎么做")
    prompt = system_prompt(model, 0)
    assert model.skills == [()]                       # 没绑定麦当劳工具
    assert SKILL in prompt and "COOKer 宿主适配" not in prompt  # 只有一行索引，没有正文


def test_load_skill_injects_body_and_binds_tools(make_graph):
    g, model = make_graph([LOAD, AIMessage("麦门管家已就位")])
    state = run(g, "我要吃麦当劳")
    assert state["skills"] == [SKILL]
    assert model.skills == [(), (SKILL,)]
    assert "七件事" in system_prompt(model, 1) and "COOKer 宿主适配" in system_prompt(model, 1)
    assert "已配置密钥" in last_tool(state)
    # 下一轮仍然生效（状态随 checkpoint 保存）
    g2_model_script = [AIMessage("好的")]
    model.script.extend(g2_model_script)
    run(g, "还在吗")
    assert model.skills[-1] == (SKILL,)


def test_mcd_tool_refused_before_skill_loaded(make_graph, fake):
    g, _ = make_graph([call("query-my-account", {}), AIMessage("…")])
    assert "load_skill" in last_tool(run(g, "我多少积分"))
    assert fake.calls == []


def test_read_tool_goes_through_mcp(make_graph, fake):
    g, _ = make_graph([LOAD, call("query-my-account", {}, "q1"), AIMessage("你有积分")])
    assert "availablePoint" in last_tool(run(g, "我多少积分"))
    assert fake.names() == ["query-my-account"]


# ---------- 客户端 ----------


def test_live_schema_validation_rejects_bad_args(fake):
    with pytest.raises(client.McdError, match="参数不符合"):
        asyncio.run(client.call("calculate-price", {"storeCode": "1"}))
    assert fake.calls == []  # 校验失败不会发出请求


def test_write_tool_cannot_be_called_as_read(fake):
    with pytest.raises(client.McdError, match="白名单"):
        asyncio.run(client.call("draw-lottery"))


def test_token_in_result_is_scrubbed(fake):
    out = asyncio.run(client.call("query-order", {"orderId": "o1"}))
    assert TOKEN not in json.dumps(out) and "[凭据已隐藏]" in json.dumps(out, ensure_ascii=False)


def test_price_is_converted_from_fen_by_code(fake):
    text = client.for_model("calculate-price", asyncio.run(client.call(
        "calculate-price", {"storeCode": "s1", "orderType": 1, "beType": 1, "items": []})))
    assert '"price":"21.00"' in text


# ---------- 写操作：确认卡 + interrupt + 台账 ----------


def draw_until_card(make_graph, extra=()):
    g, model = make_graph([LOAD, call("draw-lottery", {}, "d1"), *extra])
    run(g, "直接帮我抽一次")
    (intr,) = g.get_state(CFG).interrupts
    return g, model, intr.value


def test_draw_requires_confirmation_then_executes_once(make_graph, fake):
    g, _, card = draw_until_card(make_graph, [AIMessage("中了下单立减1元券")])
    assert card["type"] == "mcd_confirm" and ("本次消耗", "本次将消耗 24 积分") in [tuple(r) for r in card["rows"]]
    assert "draw-lottery" not in fake.names()  # 只做了只读预检

    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    result = json.loads(last_tool(state))
    assert result["status"] == "SUCCESS" and result["result"]["win"] is True
    assert fake.names().count("draw-lottery") == 1

    # 同一 tool_call_id 再执行（模拟崩溃后重放）：台账返回记录，不再发请求
    again = json.loads(asyncio.run(actions.run_write("draw-lottery", {}, "d1")))
    assert "不会重复提交" in again["message"]
    assert fake.names().count("draw-lottery") == 1


def test_cancel_executes_nothing(make_graph, fake):
    g, _, card = draw_until_card(make_graph, [AIMessage("好的，没有抽")])
    state = run(g, Command(resume={"confirmed": False, "fingerprint": card["fingerprint"]}))
    assert json.loads(last_tool(state))["status"] == "CANCELLED"
    assert "draw-lottery" not in fake.names()


def test_change_during_confirmation_blocks_execution(make_graph, fake):
    g, _, card = draw_until_card(make_graph, [AIMessage("消耗变了")])
    fake.lottery = copy.deepcopy(fake.lottery)
    fake.lottery["data"]["drawDecision"]["nextConsumption"]["text"] = "本次将消耗 48 积分"
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    assert json.loads(last_tool(state))["status"] == "CHANGED"
    assert "draw-lottery" not in fake.names()


def test_ineligible_draw_is_refused_without_card(make_graph, fake):
    fake.lottery = fixture("lottery_running_insufficient")
    g, _ = make_graph([LOAD, call("draw-lottery", {}, "d1"), AIMessage("积分不够")])
    state = run(g, "抽一次")
    assert not g.get_state(CFG).interrupts
    assert json.loads(last_tool(state))["status"] == "REFUSED"


def test_unknown_result_freezes_further_writes(make_graph, fake, monkeypatch, tmp_path):
    monkeypatch.setattr(client, "CALL_TIMEOUT", 1)
    fake.draw_delay = 3  # 服务端收到了请求，但响应超时 → 不能证明没执行
    g, _, card = draw_until_card(make_graph, [AIMessage("结果未知")])
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    assert json.loads(last_tool(state))["status"] == "UNKNOWN"

    # 冻结期间：其它写操作直接拒绝，连确认卡都不弹
    frozen = json.loads(asyncio.run(actions.run_write("auto-bind-coupons", {}, "b1")))
    assert frozen["status"] == "FROZEN" and "auto-bind-coupons" not in fake.names()

    # 人工核实后解冻
    assert Ledger(tmp_path / "ledger.sqlite3").resolve("d1", "FAILED", "App 无记录")
    monkeypatch.setattr(actions, "interrupt", lambda value: {"confirmed": False})  # 图外调用：替身代替用户点取消
    resumed = json.loads(asyncio.run(actions.run_write("auto-bind-coupons", {}, "b2")))
    assert resumed["status"] == "CANCELLED"  # 走到了确认环节，说明已解冻


def test_order_card_uses_live_price_and_checks_take_way(make_graph, fake):
    args = {"storeCode": "s1", "orderType": 1, "beType": 1, "takeWayCode": "counter",
            "items": [{"productCode": "9900008139", "quantity": 1}]}
    g, _ = make_graph([LOAD, call("create-order", args, "o1"), AIMessage("请支付")])
    run(g, "就要这个，下单")
    card = g.get_state(CFG).interrupts[0].value
    rows = dict(map(tuple, card["rows"]))
    assert rows["实付"] == "¥21.00" and rows["取餐方式"] == "柜台取餐"  # 来自实时算价（分→元）
    state = run(g, Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}))
    assert "payH5Url" in last_tool(state) and fake.names().count("create-order") == 1

    bad = json.loads(asyncio.run(actions.run_write("create-order", {**args, "takeWayCode": "drone"}, "o2")))
    assert bad["status"] == "REFUSED"


def test_async_path_used_by_langgraph_server(make_graph, fake):
    """LangGraph Server 用 ainvoke：工具走 coroutine 入口，interrupt / resume 语义相同。"""
    g, _ = make_graph([LOAD, call("draw-lottery", {}, "d1"), AIMessage("抽完了")])
    asyncio.run(g.ainvoke({"messages": [HumanMessage("抽一次")]}, CFG, context=Context()))
    card = g.get_state(CFG).interrupts[0].value
    state = asyncio.run(g.ainvoke(Command(resume={"confirmed": True, "fingerprint": card["fingerprint"]}),
                                  CFG, context=Context()))
    assert json.loads(last_tool(state))["status"] == "SUCCESS"
    assert fake.names().count("draw-lottery") == 1
