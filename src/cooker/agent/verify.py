"""回答校验（确定性，不调用 LLM）：检查最终回答引用的菜是否有工具依据。

背景：仅靠 Prompt 约束，qwen-plus 在 36 次场景评测中有 10 次「没查就答」或引用库外菜名
（见 docs/technical-decisions.md TD-17）。这里用代码检查最终回答，不通过就把具体问题反馈给模型重答。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import NamedTuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from cooker.recipes.ingredients import dish_key
from cooker.recipes.store import get_store

# agent-chat-ui 约定：id 以 do-not-render- 开头的消息不在界面上显示
CORRECTION_ID_PREFIX = "do-not-render-verify-"
CITE_RE = re.compile(r"【([^】]{1,20})】")
MIN_NAME_LEN = 3  # 「米粥」「烙饼」等两字菜名容易在普通句子里误命中，扫描时跳过

UNVERIFIED_NOTE = "\n\n> ⚠️ 以上部分内容未能与菜谱库核对，请谨慎参考。"


class Problem(NamedTuple):
    text: str
    needs_lookup: bool  # True：应先查询（重答时强制调工具）；False：应删改措辞（强制调工具没有用）


@lru_cache
def _library() -> tuple[dict[str, str], tuple[str, ...]]:
    recipes = get_store().recipes
    by_key = {dish_key(r.name): r.name for r in recipes}
    scan = tuple(sorted((r.name for r in recipes if len(r.name) >= MIN_NAME_LEN), key=len, reverse=True))
    return by_key, scan


def is_correction(msg: BaseMessage) -> bool:
    return isinstance(msg, HumanMessage) and str(msg.id or "").startswith(CORRECTION_ID_PREFIX)


def current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    """最后一条真实用户消息之后的所有消息（不含该用户消息）。"""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage) and not is_correction(messages[i]):
            return messages[i + 1 :]
    return messages


def find_problems(messages: list[BaseMessage]) -> list[Problem]:
    answer = messages[-1]
    if not isinstance(answer, AIMessage) or not isinstance(answer.content, str):
        return []
    text = answer.content
    by_key, scan = _library()
    tool_text = "\n".join(str(m.content) for m in messages if isinstance(m, ToolMessage))

    problems = []
    for cited in dict.fromkeys(CITE_RE.findall(text)):
        real = by_key.get(dish_key(cited))
        if real is None:
            problems.append(Problem(f"【{cited}】不是菜谱库里的菜：删掉它，或去掉【】作为通用举例", False))
        elif real not in tool_text:
            problems.append(Problem(f"【{cited}】没有经过工具查询：先查询再引用，或删掉它", True))
    # 没加【】但提到了库里的菜：同样必须在对话的工具结果中出现过（追问上一轮查过的菜不需要重查）
    unverified = [n for n in scan if n in text and n not in tool_text and f"【{n}】" not in text][:3]
    if unverified:
        problems.append(Problem(f"谈到了未经工具查询的菜谱（{'、'.join(unverified)}）：先调用工具查询", True))
    return problems


def correction_message(problems: list[Problem], msg_id: str) -> HumanMessage:
    body = "\n".join(f"- {p.text}" for p in problems)
    return HumanMessage(
        id=f"{CORRECTION_ID_PREFIX}{msg_id}",
        # 由 agent 节点读取：是否在重答时强制调用工具
        additional_kwargs={"require_tool": any(p.needs_lookup for p in problems)},
        content=f"【系统校验】上一版回答未通过检查：\n{body}\n请修正后重新给出完整的最终回答，"
                "保留其余正确内容，不要提及本条校验消息。",
    )
