"""COOKer Agent：显式的 ReAct 循环 + 确定性回答校验。

    START → agent ──有 tool_calls──→ tools ──→ agent
              ▲  └──无 tool_calls──→ verify ──通过──→ END
              └────────── 不通过（最多 2 次）──┘

- agent 节点：读用户档案拼系统提示词 → 裁剪历史 → 调用绑定了工具的模型（主备降级）
  技能按需加载（skills.py）：状态 skills 记录本会话已加载的技能，决定系统提示词附带哪些技能正文、绑定哪些工具
- tools 节点：langgraph 预置 ToolNode，并行执行本轮全部 tool_calls，结果作为 ToolMessage 写回
- verify 节点：检查最终回答的菜名引用是否有工具依据（verify.py），不通过则删掉该回答、附上问题让模型重答
- trace（trace.py）：三类节点各写一条执行事件（耗时、token、实际模型、是否降级、工具状态、校验结果）
- 持久化（checkpointer / store）在 compile 时注入：本地 CLI 用 SQLite，LangGraph Server 由平台提供
"""

from __future__ import annotations

import operator
import time
import uuid
from functools import lru_cache
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command

from cooker.agent.profile import load_profile
from cooker.agent.prompts import build_system_prompt
from cooker.agent import trace
from cooker.agent.tools import TOOLS, Context
from cooker.agent.verify import UNVERIFIED_NOTE, correction_message, current_turn, find_problems, is_correction
from cooker.llm import build_runnable
from cooker.llm.factory import load_models_config, resolve_provider
from cooker.skills import SKILL_TOOLS, active_tools, all_skill_tools

# 历史消息的 token 上限（近似计数）。超过时丢弃最早的轮次，且保证从一条用户消息开始，
# 避免出现「没有对应 tool_calls 的 ToolMessage」这种会被接口拒绝的残缺序列。
HISTORY_TOKEN_BUDGET = 12_000
MAX_CORRECTIONS = 2  # 每轮最多让模型重答 2 次，防止无限循环与成本失控


def _merge_skills(old: list[str], new: list[str]) -> list[str]:
    return list(dict.fromkeys(operator.add(old or [], new or [])))


class AgentState(MessagesState):
    # 本会话已加载的技能（load_skill 写入，只增不减，随 checkpoint 持久化）
    skills: Annotated[list[str], _merge_skills]
    # 执行过程事件（trace.py），只追加、保留最近 MAX_EVENTS 条
    trace: Annotated[list[dict], trace.add_trace]


@lru_cache
def get_agent_model(provider: str | None = None, require_tool: bool = False,
                    skills: tuple[str, ...] = ()) -> Runnable:
    # bind_tools 必须分别作用于主备模型（见 TD-2），所以通过 transform 传入。
    # require_tool：tool_choice="required" 让接口层强制模型本次至少调用一个工具——
    # 用于校验失败后的重答，比在 Prompt 里「要求」它调用更可靠。
    # 注意：langchain-qwq 的 bind_tools 会把 "required" 静默改写成 "auto"（base.py），
    # 而百炼接口实测支持 required，所以绑定工具后用 .bind() 直接覆盖请求参数。
    # skills：已加载技能的专用工具只在加载后绑定，做饭对话不必为几十个麦当劳工具的描述付 token
    tools = [*TOOLS, *SKILL_TOOLS, *active_tools(list(skills))]

    def transform(m):
        bound = m.bind_tools(tools)
        return bound.bind(tool_choice="required") if require_tool else bound

    return build_runnable("agent", transform=transform, provider=provider)


def call_model(state: AgentState, runtime: Runtime[Context]) -> dict:
    ctx = runtime.context or Context()
    profile = load_profile(runtime.store, ctx.user_id)
    history = trim_messages(
        state["messages"],
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=HISTORY_TOKEN_BUDGET,
        start_on="human",
        include_system=False,
    )
    # 校验反馈指出「没查就引用」时，本次重答强制先调用工具；只是「编造菜名」时强制调用没有用，改写即可
    last = state["messages"][-1]
    require_tool = is_correction(last) and bool(last.additional_kwargs.get("require_tool"))
    skills = tuple(state.get("skills") or ())
    model = get_agent_model(ctx.provider, require_tool, skills)
    started = time.time()
    response = model.invoke([SystemMessage(build_system_prompt(profile, list(skills))), *history])
    provider = resolve_provider(ctx.provider)
    event = trace.model_event(
        response, turn=trace.turn_id(state["messages"]), started=started, provider=provider,
        configured=load_models_config().providers[provider].roles.get("agent"),
        tools_bound=len(TOOLS) + len(SKILL_TOOLS) + len(active_tools(list(skills))), forced=require_tool)
    return {"messages": [response], "trace": [event]}


def route_after_agent(state: MessagesState) -> Literal["tools", "verify"]:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else "verify"


def verify(state: MessagesState) -> Command[Literal["agent", "__end__"]]:
    messages = state["messages"]
    problems = find_problems(messages)
    texts = [p.text for p in problems]
    if not problems:
        return Command(update={"trace": [trace.verify_event(messages, "pass", [])]}, goto=END)
    answer = messages[-1]
    if sum(is_correction(m) for m in current_turn(messages)) >= MAX_CORRECTIONS:
        # 重答次数用尽：保留回答但如实标注（同 id 的消息会被 add_messages 替换）
        return Command(update={"messages": [AIMessage(id=answer.id, content=answer.content + UNVERIFIED_NOTE)],
                               "trace": [trace.verify_event(messages, "give_up", texts)]}, goto=END)
    return Command(
        update={"messages": [RemoveMessage(id=answer.id), correction_message(problems, uuid.uuid4().hex[:8])],
                "trace": [trace.verify_event(messages, "retry", texts)]},
        goto="agent",
    )


def build_builder() -> StateGraph:
    builder = StateGraph(AgentState, context_schema=Context)
    builder.add_node("agent", call_model)
    # ToolNode 注册全部工具（执行端）；模型能看到哪些由 get_agent_model 按已加载技能决定（调用端）
    builder.add_node("tools", ToolNode([*TOOLS, *SKILL_TOOLS, *all_skill_tools()],
                                       wrap_tool_call=trace.wrap_tool_call, awrap_tool_call=trace.awrap_tool_call))
    builder.add_node("verify", verify)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("tools", "agent")
    return builder


def build_graph(checkpointer=None, store=None):
    return build_builder().compile(checkpointer=checkpointer, store=store, name="cooker")


# 供 langgraph dev / Aegra 加载：不带持久化，由服务端注入
graph = build_graph()
