"""执行过程记录（agent/trace.py）：模型 / 工具 / 校验事件、token 汇总、降级识别、Command 合并。"""

import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from cooker.agent import graph as graph_mod
from cooker.agent import tools as tools_mod
from cooker.agent import trace
from cooker.agent.tools import Context
from cooker.service import CookerService
from conftest import build_retriever
from test_agent import ScriptedModel, call

CFG = {"configurable": {"thread_id": "tr"}}


def with_usage(msg: AIMessage, i: int, o: int, model: str = "qwen3.7-plus") -> AIMessage:
    msg.usage_metadata = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
    msg.response_metadata = {"model_name": model}
    return msg


@pytest.fixture
def make(real_store, monkeypatch):
    svc = CookerService(real_store, build_retriever(real_store))
    monkeypatch.setattr(tools_mod, "get_service", lambda: svc)

    def _make(script):
        model = ScriptedModel(script=list(script), seen=[])
        monkeypatch.setattr(graph_mod, "get_agent_model", lambda provider=None, require_tool=False, skills=(): model)
        return graph_mod.build_graph(checkpointer=InMemorySaver(), store=InMemoryStore())

    return _make


def test_events_for_model_tool_and_verify(make):
    g = make([with_usage(call("get_recipe", {"name": "番茄炒蛋"}), 1200, 30),
              with_usage(AIMessage("做法如下……"), 2500, 200)])
    state = g.invoke({"messages": [HumanMessage("番茄炒蛋怎么做")]}, CFG, context=Context())
    kinds = [e["kind"] for e in state["trace"]]
    assert kinds == ["model", "tool", "model", "verify"]
    turn = state["messages"][0].id
    assert all(e["turn"] == turn for e in state["trace"])
    tool = state["trace"][1]
    assert tool["name"] == "get_recipe" and tool["status"] == "ok" and tool["result_chars"] > 0
    assert state["trace"][0]["tools_bound"] >= 9 and state["trace"][0]["tool_calls"] == ["get_recipe"]
    s = trace.summarize(state["trace"])
    assert (s["model_calls"], s["input_tokens"], s["output_tokens"], s["tool_calls"]) == (2, 3700, 230, 1)
    assert state["trace"][-1]["result"] == "pass"


def test_fallback_is_detected(make):
    """主厂商失败后静默降级：实际模型名与配置的主模型不同。"""
    g = make([with_usage(AIMessage("你好"), 10, 5, model="deepseek-v4.1-flash")])
    state = g.invoke({"messages": [HumanMessage("你好")]}, CFG, context=Context(provider="qwen"))
    ev = state["trace"][0]
    assert ev["model"] == "deepseek-v4.1-flash" and ev["fallback"] is True
    assert trace.summarize(state["trace"])["fallback"] is True


def test_removed_answer_tokens_are_kept(make, monkeypatch):
    """verify 删掉的回答从消息里消失，但它花的 token 仍在 trace 里。"""
    import cooker.agent.verify as verify_mod
    monkeypatch.setattr(verify_mod, "_library", lambda: ({"不存在菜": "不存在菜"}, ()))
    g = make([with_usage(AIMessage("推荐【不存在菜】"), 100, 10), with_usage(AIMessage("没有找到合适的菜"), 120, 8)])
    state = g.invoke({"messages": [HumanMessage("推荐一道菜")]}, CFG, context=Context())
    assert [e.get("result") for e in state["trace"] if e["kind"] == "verify"] == ["retry", "pass"]
    assert trace.summarize(state["trace"])["input_tokens"] == 220


def test_tool_returning_command_keeps_its_update(make):
    """load_skill 返回 Command(update={skills, messages})：包装后 skills 与 trace 都要生效。"""
    g = make([call("load_skill", {"name": "my-coffee"}, "l1"), AIMessage("好的")])
    state = g.invoke({"messages": [HumanMessage("点杯咖啡")]}, CFG, context=Context())
    assert state["skills"] == ["my-coffee"]
    assert [e["name"] for e in state["trace"] if e["kind"] == "tool"] == ["load_skill"]


def test_trace_is_capped():
    events = [{"kind": "tool", "at": time.time(), "ms": 1} for _ in range(trace.MAX_EVENTS + 50)]
    assert len(trace.add_trace(events, [{"kind": "tool", "at": 0, "ms": 0}])) == trace.MAX_EVENTS
