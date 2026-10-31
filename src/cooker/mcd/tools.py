"""把麦当劳 MCP 工具转成 LangChain 工具，供 Agent 在加载技能后绑定。

- 名称、描述、入参结构直接取自 tool_schemas.json（官方服务的快照），不手写 36 份 Pydantic 模型；
  名称与技能文档一致，模型读到的 SKILL.md 里提到哪个工具，就能直接调用哪个。
- 同时提供同步（CLI / 评测用 graph.invoke）与异步（LangGraph Server 用 ainvoke）两个入口：
  异步是真实实现；同步入口在没有事件循环的工作线程里用 asyncio.run 驱动同一个协程。
- 未加载技能时调用会被拒绝：工具只有在技能激活后才绑定给模型，这里再做一次代码层面的兜底。
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolRuntime

from cooker.mcd import actions, client
from cooker.mcd.client import WRITE_TOOLS, McdError

SKILL_NAME = "mclottery-alchemist"

WRITE_NOTE = ("\n\n【COOKer 宿主】这是写操作：调用后系统会向用户展示确认卡（金额/消耗来自实时查询），"
              "用户点「确认」才会执行，台账保证最多执行一次。参数齐全时直接调用，"
              "不要先在对话里要求用户回复「确认」。")


def _make(name: str, spec: dict) -> BaseTool:
    writing = name in WRITE_TOOLS

    async def arun(runtime: ToolRuntime, **kwargs) -> str:
        if SKILL_NAME not in ((runtime.state or {}).get("skills") or []):
            return json.dumps({"error": f"请先调用 load_skill 加载「{SKILL_NAME}」技能"}, ensure_ascii=False)
        try:
            if writing:
                return await actions.run_write(name, kwargs, runtime.tool_call_id)
            return client.for_model(name, await client.call(name, kwargs))
        except McdError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def run(runtime: ToolRuntime, **kwargs) -> str:
        return asyncio.run(arun(runtime, **kwargs))

    return StructuredTool.from_function(
        func=run, coroutine=arun, name=name,
        description=spec["description"] + (WRITE_NOTE if writing else ""),
        args_schema=spec["input_schema"],
    )


MCD_TOOLS: list[BaseTool] = [_make(name, spec) for name, spec in sorted(client.tool_schemas().items())]
MCD_TOOL_NAMES = frozenset(t.name for t in MCD_TOOLS)
