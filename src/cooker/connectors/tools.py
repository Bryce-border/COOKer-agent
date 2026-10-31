"""把连接器的 MCP 工具转成 LangChain 工具，供 Agent 在加载对应技能后绑定。

- 名称、描述、入参结构直接取自 tool_schemas.json（官方服务的快照），不手写 Pydantic 模型；
  名称与技能文档一致，模型读到的 SKILL.md 里提到哪个工具，就能直接调用哪个。
- 同时提供同步（CLI / 评测用 graph.invoke）与异步（LangGraph Server 用 ainvoke）两个入口：
  异步是真实实现；同步入口在没有事件循环的工作线程里用 asyncio.run 驱动同一个协程。
- 未加载技能时调用会被拒绝：工具只有在技能激活后才绑定给模型，这里再做一次代码层面的兜底。
- response_format="content_and_artifact"：返回 (给模型的文本, 给界面的数据)。artifact 存在 ToolMessage.artifact，
  随会话保存、前端可读，但不会发给模型——商品图片地址走这里（见 images.py）。
"""

from __future__ import annotations

import asyncio
import json

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.prebuilt import ToolRuntime

from cooker.connectors import actions, client
from cooker.connectors.base import Connector, ConnectorError
from cooker.connectors.images import extract_images

WRITE_NOTE = ("\n\n【COOKer 宿主】这是写操作：调用后系统会向用户展示确认卡（金额/状态来自实时查询），"
              "用户点「确认」才会执行，台账保证最多执行一次。参数齐全时直接调用，"
              "不要先在对话里要求用户回复「确认」。")


def _make(conn: Connector, name: str, spec: dict) -> BaseTool:
    writing = name in conn.write_tools

    async def arun(runtime: ToolRuntime, **kwargs) -> tuple[str, dict | None]:
        if conn.skill not in ((runtime.state or {}).get("skills") or []):
            return json.dumps({"error": f"请先调用 load_skill 加载「{conn.skill}」技能"}, ensure_ascii=False), None
        try:
            if writing:
                return await actions.run_write(conn, name, kwargs, runtime.tool_call_id), None
            payload = await client.call(conn, name, kwargs)
        except ConnectorError as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False), None
        images = extract_images(payload, conn.image_hosts)
        return client.for_model(conn, name, payload), ({"images": images} if images else None)

    def run(runtime: ToolRuntime, **kwargs) -> tuple[str, dict | None]:
        return asyncio.run(arun(runtime, **kwargs))

    return StructuredTool.from_function(
        func=run, coroutine=arun, name=name,
        description=(spec["description"] or name) + (WRITE_NOTE if writing else ""),
        args_schema=spec["input_schema"], response_format="content_and_artifact",
    )


def make_tools(conn: Connector) -> list[BaseTool]:
    return [_make(conn, name, spec) for name, spec in sorted(conn.schemas.items())]
