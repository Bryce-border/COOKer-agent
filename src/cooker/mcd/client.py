"""麦当劳 MCP 调用客户端。

每次调用开一个独立 MCP 会话：initialize → list_tools → 用线上入参结构校验参数 → call_tool。
- 白名单：只认 READ_TOOLS / WRITE_TOOLS 中的工具名；服务端新增的工具不会自动暴露给模型
  （尤其不能让新出现的写工具被当成只读工具直接执行）。
- 双重结构校验：tool_schemas.json 快照用于给模型绑定工具（离线、确定）；调用前再用线上结构校验，
  服务端改了必填参数时直接报错，不猜参数。
- 错误分级：McdError 的消息可以直接给用户看；写操作发出请求后才出错 → McdUnknown（结果未知，禁止重试）。
- 凭据只在本模块与 config.py 出现，返回结果里若意外出现 Token 也会被替换掉。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from jsonschema import ValidationError, validate

from cooker.mcd.config import McdSettings

SCHEMA_PATH = Path(__file__).with_name("tool_schemas.json")

# 有副作用：扣积分 / 花钱 / 改账户数据。必须经确认卡 + 台账执行（见 actions.py）
WRITE_TOOLS = frozenset({
    "create-order", "cancel-order", "mall-create-order", "party-order-create",
    "draw-lottery", "auto-bind-coupons", "delivery-create-address",
})
READ_TOOLS = frozenset({
    "now-time-info", "query-my-account", "query-lottery-info", "query-my-prizes",
    "query-mall-products", "mall-product-detail", "mall-gift-products", "mall-order-list", "mall-order-detail",
    "available-coupons", "query-my-coupons", "query-store-coupons", "campaign-calendar", "query-promotions",
    "query-nearby-stores", "delivery-query-stores", "delivery-query-addresses",
    "query-meals", "query-meal-detail", "query-meal-assistance", "calculate-price",
    "order-list", "query-order", "query-survey-coupon", "list-nutrition-foods",
    "query-party-city", "query-party-store", "query-party-store-date", "query-party-store-session",
})
CALL_TIMEOUT = 40  # 秒：整次调用（含建连与初始化）的上限
MAX_RESULT_CHARS = 16_000  # 单次结果交给模型的上限；菜单、营养表可能很长


class McdError(RuntimeError):
    """消息可直接展示给用户（不含堆栈、凭据、内部细节）。"""


class McdUnknown(McdError):
    """写请求已发出但没拿到确定结果：不能证明没执行，禁止自动重试。"""


@lru_cache
def tool_schemas() -> dict[str, dict]:
    """快照中、且在白名单内的工具：name → {description, input_schema}。"""
    items = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {t["name"]: t for t in items if t["name"] in READ_TOOLS | WRITE_TOOLS}


def load_settings() -> McdSettings:
    try:
        settings = McdSettings()
    except Exception:
        raise McdError("麦当劳配置无效，请检查本机 config/mcd-mcp.local.env") from None
    if not settings.token.get_secret_value():
        raise McdError("尚未配置麦当劳 MCP 密钥：复制 config/mcd-mcp.example.env 为 mcd-mcp.local.env 并填写 Token")
    return settings


def account_key(settings: McdSettings) -> str:
    """台账按账户隔离；用 Token 的摘要代替账户 ID，不落盘明文。"""
    return hashlib.sha256(settings.token.get_secret_value().encode()).hexdigest()[:16]


def open_transport(settings: McdSettings) -> Any:
    """测试时替换为内存中的假服务（FastMCP 实例），走同一套 MCP 协议。"""
    return StreamableHttpTransport(settings.url, auth=settings.token.get_secret_value())


async def _discard_log(*args, **kwargs) -> None:
    return None  # 不把外部服务推送的日志写进本地日志


async def call(name: str, arguments: dict | None = None, *, writing: bool = False) -> Any:
    """调用一个麦当劳工具，返回解析后的数据（dict / list / 文本）。

    writing=True 只应由 actions.execute 在台账认领之后使用。
    """
    allowed = WRITE_TOOLS if writing else READ_TOOLS
    if name not in allowed:
        raise McdError(f"「{name}」不在允许的{'写' if writing else '只读'}工具白名单内")
    arguments = arguments or {}
    settings = await asyncio.to_thread(load_settings)  # 读配置文件是阻塞 IO，移出事件循环
    dispatched = False
    try:
        async with asyncio.timeout(CALL_TIMEOUT):
            async with Client(open_transport(settings), timeout=25, init_timeout=15,
                              log_handler=_discard_log) as client:
                live = {t.name: t for t in await client.list_tools()}
                if name not in live:
                    raise McdError(f"麦当劳服务当前没有提供「{name}」")
                try:
                    validate(arguments, _schema_of(live[name]))
                except ValidationError as e:
                    raise McdError(f"「{name}」参数不符合服务端要求：{e.message}") from None
                dispatched = writing
                result = await client.call_tool(name, arguments, raise_on_error=False)
    except McdError:
        raise
    except Exception:
        if dispatched:
            raise McdUnknown("请求已发出但未收到确定结果（超时或断连）。不能确认是否已执行，"
                             "已冻结后续写操作；请在麦当劳 App 核对订单、积分与记录，切勿重复提交。") from None
        raise McdError("麦当劳服务暂不可用：请检查网络、密钥是否过期，或稍后重试") from None
    if result.is_error:
        text = _text(result)
        if dispatched:  # 服务端明确返回错误：视为未执行（不是超时）
            return {"success": False, "message": text[:500] or "服务端返回错误"}
        raise McdError(f"「{name}」调用失败：{text[:300] or '服务端返回错误'}")
    return _scrub(_decode(result), settings.token.get_secret_value())


def _text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text").strip()


def _decode(result: Any) -> Any:
    payload = result.structured_content
    if payload is None:
        text = _text(result)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text  # 部分工具返回 Markdown / TOON 文本
    # 部分 MCP 服务把唯一返回值包在 result 键里
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                pass
    return payload


def _scrub(payload: Any, token: str) -> Any:
    raw = json.dumps(payload, ensure_ascii=False)
    if token and token in raw:
        return json.loads(raw.replace(token, "[凭据已隐藏]"))
    return payload


def data_of(payload: Any) -> Any:
    """取业务数据：多数接口是 {success, code, data}。"""
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def business_error(payload: Any) -> str | None:
    """服务端明确拒绝时返回原因（原样转述，不改写）；正常返回 None。

    draw-lottery 失败时 success 仍为 true，要看 data.status.code。
    """
    if not isinstance(payload, dict):
        return None
    status = data_of(payload).get("status") if isinstance(data_of(payload), dict) else None
    if isinstance(status, dict) and status.get("code") not in (None, "SUCCESS"):
        return str(status.get("message") or status.get("code"))
    if payload.get("success") is False or payload.get("code") not in (None, 0, 200, "0", "200", "SUCCESS"):
        return str(payload.get("message") or payload.get("msg") or "服务端返回失败")
    return None


# calculate-price 的金额字段单位是「分」，create-order 用「元」，混用差 100 倍 —— 由代码换算，不让模型心算
_FEN_FIELDS = ("productOriginalPrice", "productPrice", "deliveryOriginalPrice", "deliveryPrice",
               "originalPrice", "discount", "price", "tablewarePrice")


def yuan(fen: Any) -> str | None:
    try:
        return f"{int(fen) / 100:.2f}"
    except (TypeError, ValueError):
        return None


def for_model(name: str, payload: Any) -> str:
    """把结果整理成交给模型的文本：补充单位换算，并限制长度。"""
    data = data_of(payload)
    if name == "calculate-price" and isinstance(data, dict):
        data = {**data, "金额换算（元，由代码按分÷100 计算）": {
            k: yuan(data[k]) for k in _FEN_FIELDS if k in data}}
        payload = {**payload, "data": data} if isinstance(payload, dict) and "data" in payload else data
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"…（结果过长，已截断，原长 {len(text)} 字符；需要时缩小查询范围）"
    return text


def _schema_of(tool: Any) -> dict:
    # MCP SDK v2 把 inputSchema 改名为 input_schema；两种都兼容
    return getattr(tool, "input_schema", None) or tool.inputSchema
