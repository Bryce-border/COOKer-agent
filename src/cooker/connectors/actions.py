"""写操作（所有连接器共用）：确认卡 → interrupt → 台账认领 → 执行一次 → 结算。

为什么确认卡由代码生成，而不是让模型写：卡片上的金额、消耗、订单状态都来自刚刚的实时查询
（下单前重新算价 / 预览、抽奖前重新读资格），模型说错了也不会被用户「确认」进去。
每个服务的卡片怎么生成写在各自的 connector.py（Connector.cards）。

interrupt 的语义（与 update_profile 移除过敏原相同）：图在 interrupt() 处暂停，前端用
Command(resume=…) 恢复时，本工具会**从头重新执行**。所以：
1. interrupt 之前只能有只读操作（查询、建卡），不能有写操作；
2. 重跑时卡片会重新生成。卡片内容的指纹随中断发给前端，用户确认时带回来；
   两次指纹不一致（确认期间价格 / 消耗变了）就不执行，让用户看新信息重新确认。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from jsonschema import ValidationError, validate
from langgraph.types import interrupt

from cooker.connectors import client
from cooker.connectors.base import Card, Connector, ConnectorError, Refuse, Unknown, data_of
from cooker.connectors.ledger import Frozen, Ledger


async def read(conn: Connector, name: str, args: dict | None = None) -> Any:
    """建卡用的只读查询：服务端拒绝时不弹卡。"""
    payload = await client.call(conn, name, args)
    if err := conn.business_error(payload):
        raise Refuse(f"{name} 查询失败：{err}")
    return data_of(payload)


def fingerprint(conn: Connector, name: str, args: dict, card: Card) -> str:
    raw = json.dumps({"connector": conn.key, "tool": name, "args": args, "rows": card.rows, "facts": card.facts},
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _out(status: str, message: str, result: Any = None) -> str:
    body: dict[str, Any] = {"status": status, "message": message}
    if result is not None:
        body["result"] = result
    return json.dumps(body, ensure_ascii=False)


async def run_write(conn: Connector, name: str, args: dict, call_id: str) -> str:
    """执行一个写工具，返回交给模型的结果文本。call_id 是 tool_call_id，作为台账幂等键。"""
    creds = await asyncio.to_thread(client.load_credentials, conn)
    account = client.account_key(conn, creds)
    ledger = await asyncio.to_thread(Ledger)
    # 1. 同一次工具调用已经执行过（崩溃后重放）：只返回记录，绝不重发
    if done := await asyncio.to_thread(ledger.get, call_id):
        return _out(done["status"], "这次操作此前已执行过，以下是台账中的记录，不会重复提交", done["result"])
    # 2. 冻结时直接拒绝，免得用户确认了一张注定无法执行的卡
    if pending := await asyncio.to_thread(ledger.pending, account):
        return _out("FROZEN", f"{conn.label}账户有一笔结果未知的操作（{pending}），已冻结所有写操作。"
                              f"请用户先在{conn.app_hint}核实，再由本人执行台账命令解冻。")
    try:
        validate(args, conn.schemas[name]["input_schema"])
        card = await conn.cards[name](conn, args)
    except ValidationError as e:
        return _out("INVALID", f"参数不符合要求：{e.message}")
    except Refuse as e:
        return _out("REFUSED", str(e))
    final_args = card.args if card.args is not None else args  # 卡片可以用实时数据补全参数（如券码透传）

    fp = fingerprint(conn, name, final_args, card)
    answer = interrupt({"type": "action_confirm", "connector": conn.key, "tool": name, "title": card.title,
                        "rows": card.rows, "warnings": card.warnings, "fingerprint": fp})
    if not (isinstance(answer, dict) and answer.get("confirmed") is True):
        return _out("CANCELLED", "用户取消：什么都没执行，金额和积分没有变动")
    if answer.get("fingerprint") != fp:
        return _out("CHANGED", "确认期间实时信息发生了变化（如价格、消耗或订单状态），本次未执行。"
                               "请把最新情况告诉用户，由用户重新发起")

    # 3. 认领（原子）→ 发请求 → 结算
    try:
        if done := await asyncio.to_thread(ledger.begin, call_id, account, name, final_args, fp):
            return _out(done["status"], "这次操作此前已执行过，不会重复提交", done["result"])
    except Frozen as e:
        return _out("FROZEN", str(e))
    try:
        payload = await client.call(conn, name, final_args, writing=True)
    except Unknown as e:
        await asyncio.to_thread(ledger.settle, call_id, "UNKNOWN", {"error": str(e)})
        return _out("UNKNOWN", f"{e} 台账记录号：{call_id}")
    except ConnectorError as e:  # 请求发出前失败（连接、配置、参数）：确定未执行
        await asyncio.to_thread(ledger.settle, call_id, "FAILED", {"error": str(e)})
        return _out("FAILED", f"未执行：{e}")
    if err := conn.business_error(payload):
        await asyncio.to_thread(ledger.settle, call_id, "FAILED", payload)
        return _out("FAILED", f"服务端拒绝（原样转述）：{err}", data_of(payload))
    await asyncio.to_thread(ledger.settle, call_id, "SUCCESS", payload)  # 台账存完整原始返回，便于对账
    return _out("SUCCESS", "已执行一次", data_of(conn.present(name, payload)))
