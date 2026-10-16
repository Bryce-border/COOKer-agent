"""Agent 图的离线测试：用按剧本输出的假模型驱动真实的图、ToolNode、Store 与 interrupt。"""

import json

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from conftest import build_retriever
from cooker.agent import graph as graph_mod
from cooker.agent import tools as tools_mod
from cooker.agent.profile import UserProfile, load_profile, save_profile
from cooker.agent.tools import Context
from cooker.service import CookerService


class ScriptedModel(BaseChatModel):
    """依次返回预设的 AIMessage；记录每次收到的消息，便于断言系统提示词等输入。"""

    script: list
    seen: list = []
    forced: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name, args, id_="c1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": id_, "type": "tool_call"}])


@pytest.fixture
def env(real_store, monkeypatch):
    svc = CookerService(real_store, build_retriever(real_store))
    monkeypatch.setattr(tools_mod, "get_service", lambda: svc)
    store = InMemoryStore()

    def make(script):
        model = ScriptedModel(script=list(script), seen=[])
        model.forced = []
        def get_model(provider=None, require_tool=False):
            model.forced.append(require_tool)
            return model
        monkeypatch.setattr(graph_mod, "get_agent_model", get_model)
        g = graph_mod.build_graph(checkpointer=InMemorySaver(), store=store)
        return g, model

    return make, store


def tool_results(state) -> list[dict]:
    return [json.loads(m.content) for m in state["messages"] if isinstance(m, ToolMessage)]


CFG = {"configurable": {"thread_id": "t1"}}


def test_react_loop_runs_tool_then_answers(env):
    make, _ = env
    g, model = make([call("get_recipe", {"name": "番茄炒蛋"}), AIMessage("做法如下……")])
    state = g.invoke({"messages": [HumanMessage("番茄炒蛋怎么做")]}, CFG, context=Context())
    assert state["messages"][-1].content == "做法如下……"
    assert tool_results(state)[0]["summary"]["name"] == "西红柿炒鸡蛋"
    assert len(model.seen) == 2  # 模型被调用两次：决定调工具 → 看到结果后作答
    assert isinstance(model.seen[1][-1], ToolMessage)


def test_profile_is_injected_and_enforced(env):
    make, store = env
    save_profile(store, "local", UserProfile(allergies=["蛋"], dislikes=["香菜"]))
    g, model = make([call("search_recipes", {"query": "炒蛋", "limit": 8}), AIMessage("ok")])
    state = g.invoke({"messages": [HumanMessage("推荐个炒蛋")]}, CFG, context=Context())
    assert "过敏：蛋" in model.seen[0][0].content  # 档案进入系统提示词
    out = tool_results(state)[0]
    assert out["auto_applied"]["排除过敏原"] == ["蛋"]
    assert out["results"] and all("蛋" not in r["recipe"]["allergens"] for r in out["results"])  # 模型没传也被排除


def test_get_recipe_warns_on_conflict(env):
    make, store = env
    save_profile(store, "local", UserProfile(allergies=["蛋"]))
    g, _ = make([call("get_recipe", {"name": "西红柿炒鸡蛋"}), AIMessage("ok")])
    state = g.invoke({"messages": [HumanMessage("西红柿炒鸡蛋")]}, CFG, context=Context())
    assert "profile_warning" in tool_results(state)[0]


def test_update_profile_and_isolation_by_user(env):
    make, store = env
    g, _ = make([call("update_profile", {"add_allergies": ["花生"], "household_size": 3}), AIMessage("记住了")])
    g.invoke({"messages": [HumanMessage("我花生过敏，家里三口人")]}, CFG, context=Context(user_id="alice"))
    assert load_profile(store, "alice").allergies == ["花生"]
    assert load_profile(store, "alice").household_size == 3
    assert load_profile(store, "local").is_empty()  # namespace 按用户隔离


@pytest.mark.parametrize("answer, expected", [("否", ["花生"]), ("是", [])])
def test_removing_allergy_requires_confirmation(env, answer, expected):
    make, store = env
    save_profile(store, "local", UserProfile(allergies=["花生"]))
    g, _ = make([call("update_profile", {"remove_allergies": ["花生"]}), AIMessage("好的")])
    g.invoke({"messages": [HumanMessage("我其实不对花生过敏")]}, CFG, context=Context())
    st = g.get_state(CFG)
    assert st.interrupts and "花生" in st.interrupts[0].value["question"]  # 图暂停，等待确认
    assert load_profile(store, "local").allergies == ["花生"]               # 暂停期间未写入
    g.invoke(Command(resume=answer), CFG, context=Context())
    assert load_profile(store, "local").allergies == expected
    assert g.get_state(CFG).interrupts == ()


def test_tool_error_is_returned_to_model(env):
    make, _ = env
    g, model = make([call("get_recipe", {"name": "不存在的菜"}), AIMessage("没找到")])
    state = g.invoke({"messages": [HumanMessage("x")]}, CFG, context=Context())
    assert "没有找到菜谱" in tool_results(state)[0]["error"]
    assert state["messages"][-1].content == "没找到"  # 错误没有中断循环


def test_scale_uses_profile_household_size(env):
    make, store = env
    save_profile(store, "local", UserProfile(household_size=4))
    g, _ = make([call("scale_recipe", {"name": "可乐鸡翅"}), AIMessage("ok")])
    state = g.invoke({"messages": [HumanMessage("可乐鸡翅要多少料")]}, CFG, context=Context())
    assert tool_results(state)[0]["people"] == 4


def test_history_survives_across_turns(env):
    make, _ = env
    g, model = make([AIMessage("你好"), AIMessage("还记得")])
    g.invoke({"messages": [HumanMessage("第一句")]}, CFG, context=Context())
    g.invoke({"messages": [HumanMessage("第二句")]}, CFG, context=Context())
    contents = [m.content for m in model.seen[1]]
    assert "第一句" in contents and "你好" in contents  # checkpointer 恢复了上一轮


# ---------- verify 节点 ----------


def test_verify_rejects_uncited_dish_then_accepts(env):
    make, _ = env
    g, model = make([
        AIMessage("推荐【佛跳墙】和【西红柿炒鸡蛋】"),                     # 没查就答 + 库外菜名
        call("get_recipe", {"name": "西红柿炒鸡蛋"}),
        AIMessage("推荐【西红柿炒鸡蛋】"),
    ])
    state = g.invoke({"messages": [HumanMessage("推荐个菜")]}, CFG, context=Context())
    texts = [m.content for m in state["messages"]]
    assert state["messages"][-1].content == "推荐【西红柿炒鸡蛋】"
    assert "推荐【佛跳墙】和【西红柿炒鸡蛋】" not in texts       # 被驳回的回答已从状态删除
    feedback = model.seen[1][-1].content
    assert "【佛跳墙】不是菜谱库里的菜" in feedback and "【西红柿炒鸡蛋】没有经过工具查询" in feedback
    assert model.seen[1][-1].additional_kwargs["require_tool"] is True
    assert any(str(m.id).startswith("do-not-render-") for m in state["messages"])  # 校验消息对前端隐藏
    assert model.forced == [False, True, False]  # 只有校验失败后的那次调用强制使用工具


def test_verify_gives_up_after_max_corrections(env):
    make, _ = env
    g, model = make([AIMessage("【不存在的菜A】"), AIMessage("【不存在的菜B】"), AIMessage("【不存在的菜C】")])
    state = g.invoke({"messages": [HumanMessage("x")]}, CFG, context=Context())
    assert len(model.seen) == 3                       # 首答 + 2 次重答
    assert "未能与菜谱库核对" in state["messages"][-1].content


def test_verify_passes_plain_chat(env):
    make, _ = env
    g, model = make([AIMessage("不客气，祝你做饭顺利！")])
    g.invoke({"messages": [HumanMessage("谢谢")]}, CFG, context=Context())
    assert len(model.seen) == 1


def test_verify_allows_citing_earlier_turn(env):
    make, _ = env
    g, _ = make([call("get_recipe", {"name": "可乐鸡翅"}), AIMessage("【可乐鸡翅】做法……"),
                 AIMessage("【可乐鸡翅】可以少放糖")])
    g.invoke({"messages": [HumanMessage("可乐鸡翅怎么做")]}, CFG, context=Context())
    state = g.invoke({"messages": [HumanMessage("能少放糖吗")]}, CFG, context=Context())
    assert state["messages"][-1].content == "【可乐鸡翅】可以少放糖"  # 上一轮查过的菜可直接引用


def test_sqlite_persistence_end_to_end(tmp_path, real_store, monkeypatch):
    """用真实 SQLite saver + store（而非内存版）：覆盖共用连接导致的事务冲突这类问题。"""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.sqlite import SqliteStore

    from cooker.agent.cli import _connect

    svc = CookerService(real_store, build_retriever(real_store))
    monkeypatch.setattr(tools_mod, "get_service", lambda: svc)
    script = [call("update_profile", {"add_allergies": ["花生"]}), AIMessage("记住了"),
              call("update_profile", {"remove_allergies": ["花生"]}), AIMessage("已移除")]
    model = ScriptedModel(script=script, seen=[])
    monkeypatch.setattr(graph_mod, "get_agent_model", lambda provider=None, require_tool=False: model)

    db = tmp_path / "agent.db"
    saver, store = SqliteSaver(_connect(db)), SqliteStore(_connect(db, autocommit=True))
    saver.setup(), store.setup()
    g = graph_mod.build_graph(checkpointer=saver, store=store)
    g.invoke({"messages": [HumanMessage("我花生过敏")]}, CFG, context=Context())
    g.invoke({"messages": [HumanMessage("删掉花生")]}, CFG, context=Context())
    assert g.get_state(CFG).interrupts                     # 中断状态已持久化
    g.invoke(Command(resume="是"), CFG, context=Context())
    assert load_profile(store, "local").allergies == []

    # 重新打开数据库：会话历史与档案都还在
    store2 = SqliteStore(_connect(db, autocommit=True))
    save_profile(store2, "local", UserProfile(dislikes=["香菜"]))
    g2 = graph_mod.build_graph(checkpointer=SqliteSaver(_connect(db)), store=store2)
    assert len(g2.get_state(CFG).values["messages"]) >= 6
    assert load_profile(store, "local").dislikes == ["香菜"]


def test_remove_allergy_not_in_profile_is_reported(env):
    make, store = env
    g, _ = make([call("update_profile", {"remove_allergies": ["花生"]}), AIMessage("ok")])
    state = g.invoke({"messages": [HumanMessage("我不对花生过敏")]}, CFG, context=Context())
    assert not g.get_state(CFG).interrupts                    # 不需要确认
    assert "本来就没有" in tool_results(state)[0]["note"]


def test_fabricated_only_does_not_force_tool(env):
    make, _ = env
    g, model = make([call("search_cooking_tips", {"query": "油温"}), AIMessage("例如【炸春卷】用七成热"),
                     AIMessage("例如炸春卷用七成热")])
    state = g.invoke({"messages": [HumanMessage("油温怎么判断")]}, CFG, context=Context())
    assert state["messages"][-1].content == "例如炸春卷用七成热"
    assert model.forced == [False, False, False]  # 只有编造菜名 → 改写即可，不强制调工具
