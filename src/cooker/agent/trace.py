"""执行过程记录（trace）：每次模型调用、工具调用、回答校验各记一条事件，存在图状态 trace 里。

为什么放进图状态而不是日志：随 checkpoint 持久化、前端能按会话读取；verify 删掉的那版回答
（RemoveMessage）虽然从消息里消失了，它花掉的 token 仍留在 trace 里，统计才是真实的。
数据只在本机（checkpoint），不发给任何外部服务。

事件结构（都带 turn = 本轮用户消息的 id、at = 开始时间戳、ms = 耗时）：
- model：provider（请求的厂商）、model（实际返回的模型名）、fallback（是否降级到备用厂商）、
         input_tokens / output_tokens / cache_read、tools_bound（本次绑定的工具数）、tool_calls、forced
- tool：name、status（ok / error / 写操作的 SUCCESS、CANCELLED…）、result_chars
- verify：result（pass / retry / give_up）、problems
"""

from __future__ import annotations

import dataclasses
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

MAX_EVENTS = 400  # 每个会话只保留最近的事件，防止 checkpoint 无限增长
HIDDEN_PREFIX = "do-not-render-"  # verify 插入的隐藏用户消息


def add_trace(old: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """reducer：追加并截断。"""
    return [*(old or []), *(new or [])][-MAX_EVENTS:]


def turn_id(messages: list[BaseMessage]) -> str | None:
    for m in reversed(messages):
        if isinstance(m, HumanMessage) and not str(m.id or "").startswith(HIDDEN_PREFIX):
            return m.id
    return None


def model_event(response: AIMessage, *, turn: str | None, started: float, provider: str,
                configured: str | None, tools_bound: int, forced: bool) -> dict:
    usage = response.usage_metadata or {}
    actual = response.response_metadata.get("model_name") or response.response_metadata.get("model")
    return {
        "kind": "model", "turn": turn, "at": started, "ms": round((time.time() - started) * 1000),
        "provider": provider, "model": actual or configured,
        # 主厂商失败后 with_fallbacks 会静默切到备用厂商：比较实际模型名与配置的主模型
        "fallback": bool(actual and configured and actual != configured),
        "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
        "cache_read": (usage.get("input_token_details") or {}).get("cache_read"),
        "tools_bound": tools_bound, "tool_calls": [tc["name"] for tc in response.tool_calls], "forced": forced,
    }


def _tool_status(msg: ToolMessage) -> str:
    if msg.status == "error":
        return "error"
    try:
        body = json.loads(msg.content) if isinstance(msg.content, str) else None
    except json.JSONDecodeError:
        return "ok"
    if isinstance(body, dict):
        if "error" in body:
            return "error"
        if isinstance(body.get("status"), str):  # 外部服务写操作：SUCCESS / CANCELLED / REFUSED …
            return body["status"]
    return "ok"


def _with_event(result: ToolMessage | Command, request: Any, started: float) -> Command:
    msg = result if isinstance(result, ToolMessage) else next(
        (m for m in ((result.update or {}).get("messages") or []) if isinstance(m, ToolMessage)), None)
    event = {
        "kind": "tool", "turn": turn_id(request.state.get("messages", [])) if isinstance(request.state, dict) else None,
        "at": started, "ms": round((time.time() - started) * 1000), "name": request.tool_call["name"],
        "status": _tool_status(msg) if msg else "ok", "result_chars": len(str(msg.content)) if msg else 0,
    }
    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], "trace": [event]})
    update = dict(result.update or {})  # 工具本身返回 Command（如 load_skill）：在原 update 上追加
    update["trace"] = [*(update.get("trace") or []), event]
    return dataclasses.replace(result, update=update)


def wrap_tool_call(request, execute):
    started = time.time()
    return _with_event(execute(request), request, started)


async def awrap_tool_call(request, execute):
    started = time.time()
    return _with_event(await execute(request), request, started)


def verify_event(messages: list[BaseMessage], result: str, problems: list[str]) -> dict:
    return {"kind": "verify", "turn": turn_id(messages), "at": time.time(), "ms": 0,
            "result": result, "problems": problems}


def summarize(events: list[dict]) -> dict:
    """一轮（或一个会话）的汇总：总耗时、模型调用次数、token、工具次数、是否发生降级。"""
    models = [e for e in events if e["kind"] == "model"]
    start = min((e["at"] for e in events), default=0)
    end = max((e["at"] + e["ms"] / 1000 for e in events), default=0)
    return {
        "seconds": round(end - start, 1), "model_calls": len(models),
        "input_tokens": sum(e.get("input_tokens") or 0 for e in models),
        "output_tokens": sum(e.get("output_tokens") or 0 for e in models),
        "tool_calls": sum(e["kind"] == "tool" for e in events),
        "models": sorted({e["model"] for e in models if e.get("model")}),
        "fallback": any(e.get("fallback") for e in models),
        "retries": sum(e["kind"] == "verify" and e["result"] == "retry" for e in events),
    }
