"""外部 MCP 服务调用客户端（所有连接器共用）。

每次调用开一个独立 MCP 会话：initialize → list_tools → 用线上入参结构校验参数 → call_tool。
- 白名单：只认连接器声明的读 / 写工具；服务端新增的工具不会自动暴露给模型
  （尤其不能让新出现的写工具被当成只读工具直接执行）。
- 双重结构校验：tool_schemas.json 快照用于给模型绑定工具（离线、确定）；调用前再用线上结构校验，
  服务端改了必填参数时直接报错，不猜参数。
- 错误分级：ConnectorError 的消息可以直接给用户看；写请求发出后才出错 → Unknown（结果未知，禁止重试）。
- 凭据只在本模块与 Connector.credentials 出现，返回结果里若意外出现 Token 也会被替换掉。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from jsonschema import ValidationError, validate

from cooker.connectors.base import Connector, ConnectorError, Credentials, Unknown
from cooker.connectors.images import strip_images

CALL_TIMEOUT = 40  # 秒：整次调用（含建连与初始化）的上限
MAX_RESULT_CHARS = 16_000  # 单次结果交给模型的上限；菜单、营养表可能很长


def load_credentials(conn: Connector) -> Credentials:
    """测试时替换为假凭据。"""
    return conn.credentials()


def account_key(conn: Connector, creds: Credentials) -> str:
    """台账按「服务 + 账户」隔离；用 Token 的摘要代替账户 ID，不落盘明文。"""
    return conn.key + ":" + hashlib.sha256(creds.token.encode()).hexdigest()[:16]


def open_transport(conn: Connector, creds: Credentials) -> Any:
    """测试时替换为内存中的假服务（FastMCP 实例），走同一套 MCP 协议。"""
    return StreamableHttpTransport(creds.url, auth=creds.token)


async def _discard_log(*args, **kwargs) -> None:
    return None  # 不把外部服务推送的日志写进本地日志


async def call(conn: Connector, name: str, arguments: dict | None = None, *, writing: bool = False) -> Any:
    """调用一个工具，返回解析后的数据（dict / list / 文本）。

    writing=True 只应由 actions.run_write 在台账认领之后使用。
    """
    allowed = conn.write_tools if writing else conn.read_tools
    if name not in allowed:
        raise ConnectorError(f"「{name}」不在{conn.label}允许的{'写' if writing else '只读'}工具白名单内")
    arguments = arguments or {}
    creds = await asyncio.to_thread(load_credentials, conn)  # 读配置文件是阻塞 IO，移出事件循环
    dispatched = False
    try:
        async with asyncio.timeout(CALL_TIMEOUT):
            async with Client(open_transport(conn, creds), timeout=25, init_timeout=15,
                              log_handler=_discard_log) as client:
                live = {t.name: t for t in await client.list_tools()}
                if name not in live:
                    raise ConnectorError(f"{conn.label}服务当前没有提供「{name}」")
                try:
                    validate(arguments, schema_of(live[name]))
                except ValidationError as e:
                    raise ConnectorError(f"「{name}」参数不符合服务端要求：{e.message}") from None
                dispatched = writing
                result = await client.call_tool(name, arguments, raise_on_error=False)
    except ConnectorError:
        raise
    except Exception:
        if dispatched:
            raise Unknown(f"请求已发出但未收到确定结果（超时或断连）。不能确认是否已执行，已冻结{conn.label}的后续写操作；"
                          f"请在{conn.app_hint}核对订单与记录，切勿重复提交。") from None
        raise ConnectorError(f"{conn.label}服务暂不可用：请检查网络、密钥是否过期，或稍后重试") from None
    if result.is_error:
        text = _text(result)
        if dispatched:  # 服务端明确返回错误：视为未执行（不是超时）
            return {"success": False, "message": text[:500] or "服务端返回错误"}
        raise ConnectorError(f"「{name}」调用失败：{text[:300] or '服务端返回错误'}")
    return _scrub(_decode(result), creds.token)


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


def for_model(conn: Connector, name: str, payload: Any) -> str:
    """把结果整理成交给模型的文本：服务特有处理（如单位换算）、去掉图片地址（界面另行展示），再限制长度。"""
    payload = strip_images(conn.present(name, payload))
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"…（结果过长，已截断，原长 {len(text)} 字符；需要时缩小查询范围）"
    return text


def schema_of(tool: Any) -> dict:
    # MCP SDK v2 把 inputSchema 改名为 input_schema；两种都兼容
    return getattr(tool, "input_schema", None) or tool.inputSchema
