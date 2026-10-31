"""麦当劳写操作：确认卡 → interrupt → 台账认领 → 执行一次 → 结算。

为什么确认卡由代码生成，而不是让模型写：卡片上的金额、消耗、订单状态都来自刚刚的实时查询
（下单前重新算价、抽奖前重新读 drawDecision），模型说错了也不会被用户「确认」进去。

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
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import ValidationError, validate
from langgraph.types import interrupt

from cooker.mcd import client
from cooker.mcd.client import McdError, McdUnknown, business_error, data_of, yuan
from cooker.mcd.ledger import Frozen, Ledger

BE_TYPES = {1: "到店取餐", 2: "麦乐送", 5: "得来速", 6: "企业团餐"}
CANCEL_REASONS = {"1": "改主意了", "2": "重复下单", "3": "点错了/点多了/点少了",
                  "4": "地址/电话填错了", "5": "送达时间选错了", "-1": "其它原因"}
PRICE_KEYS = ("storeCode", "beCode", "orderType", "beType", "reservationDate", "gmServiceCode",
              "withOrder", "needTableware", "items")


class Refuse(Exception):
    """前置条件不满足：不弹确认卡，把原因告诉模型（由它转告用户或补全参数）。"""


@dataclass
class Card:
    title: str
    rows: list[tuple[str, str]]
    warnings: list[str] = field(default_factory=list)
    facts: Any = None  # 参与指纹的实时数据（价格、消耗、订单状态）


def _money(fen: Any) -> str:
    v = yuan(fen)
    return f"¥{v}" if v is not None else "数据缺失"


async def _read(name: str, args: dict | None = None) -> Any:
    payload = await client.call(name, args)
    if err := business_error(payload):
        raise Refuse(f"{name} 查询失败：{err}")
    return data_of(payload)


async def _create_order(a: dict) -> Card:
    order_type = a.get("orderType")
    if order_type == 2 and not a.get("addressId"):
        raise Refuse("外送订单缺少 addressId：先用 delivery-query-addresses 让用户选择地址")
    if order_type == 1 and not a.get("takeWayCode"):
        raise Refuse("到店订单缺少 takeWayCode：取值只能来自 calculate-price 返回的 takeWayList")
    d = await _read("calculate-price", {k: a[k] for k in PRICE_KEYS if k in a})
    ways = {w.get("code"): w.get("name") for w in d.get("takeWayList") or []}
    if order_type == 1 and a["takeWayCode"] not in ways:
        raise Refuse(f"takeWayCode「{a['takeWayCode']}」不在本次算价返回的取餐方式 {list(ways)} 中")
    items = [f"{p.get('productName')} × {p.get('quantity')}　{_money(p.get('subtotal'))}"
             for p in d.get("productList") or []]
    rows = [("方式", BE_TYPES.get(a.get("beType"), str(a.get("beType")))),
            ("门店编码", str(a.get("storeCode"))),
            ("取餐方式", ways.get(a.get("takeWayCode"), "") if order_type == 1 else "外送到所选地址"),
            ("商品", "\n".join(items) or "数据缺失"),
            ("商品金额", _money(d.get("productPrice"))),
            ("配送费", _money(d.get("deliveryPrice"))),
            ("优惠", _money(d.get("discount"))),
            ("实付", _money(d.get("price")))]
    rows += [(k, str(a[f])) for k, f in (("预约时间", "reservationDate"), ("备注", "remark")) if a.get(f)]
    return Card("确认下单", rows,
                ["金额来自刚刚的实时算价；下单后需在麦当劳官方支付页完成支付，COOKer 不代付"],
                facts={k: d.get(k) for k in ("price", "discount", "deliveryPrice", "productList")})


async def _cancel_order(a: dict) -> Card:
    reason = str(a.get("cancelReasonCode"))
    if reason not in CANCEL_REASONS:
        raise Refuse(f"cancelReasonCode 只能取 {list(CANCEL_REASONS)}")
    d = await _read("query-order", {"orderId": a["orderId"]})
    detail = d.get("orderDetail", d) if isinstance(d, dict) else {}
    status = detail.get("orderStatus", "数据缺失")
    warnings = [] if status == "待支付" else ["订单已不是「待支付」：能否取消、退款何时到账以麦当劳官方为准"]
    return Card("确认取消订单", [
        ("订单号", str(a["orderId"])), ("当前状态", str(status)),
        ("门店", str(detail.get("storeName", "数据缺失"))),
        ("实付", f"¥{detail['realTotalAmount']}" if detail.get("realTotalAmount") else "数据缺失"),
        ("取消原因", CANCEL_REASONS[reason])], warnings, facts={"status": status})


async def _draw_lottery(a: dict) -> Card:
    d = await _read("query-lottery-info")
    if d.get("activityStatusText") != "进行中":
        raise Refuse(f"暂不可抽：活动状态为「{d.get('activityStatusText', '数据缺失')}」")
    decision = d.get("drawDecision") or {}
    if decision.get("resourceEligible") is not True:  # 资格只看服务端判定（技能 §3.4）
        raise Refuse(f"暂不可抽：{decision.get('reason') or '服务端判定积分/次数不足'}")
    nxt, fallback = decision.get("nextConsumption") or {}, decision.get("fallbackConsumption")
    rows = [("活动", str(d.get("activityName", "数据缺失"))), ("次数", "1 次"),
            ("本次消耗", str(nxt.get("text", "数据缺失"))),
            ("当前可用积分", str(d.get("availablePoint", "数据缺失")))]
    if fallback:
        rows.append(("次数用完后", str(fallback.get("text", fallback))))
    return Card("确认抽奖", rows, ["接口不提供中奖概率；结果未知时会冻结，不会自动重试"],
                facts={"activity": d.get("activityCode"), "next": nxt, "fallback": fallback})


async def _mall_create_order(a: dict) -> Card:
    category = str(a.get("spuCategory"))
    if category not in ("1", "2"):
        raise Refuse('spuCategory 只能是 "1"（虚拟）或 "2"（实物），取自商品详情')
    if category == "2" and not a.get("addressId"):
        raise Refuse("实物商品必须先有收货地址：用 delivery-query-addresses 让用户选择，不能先下单后补地址")
    account = await _read("query-my-account")
    rows = [("商品 SKU", str(a["skuId"])), ("数量", str(a.get("count", 1))),
            ("类型", "实物（寄送到地址）" if category == "2" else "虚拟商品/券"),
            ("当前可用积分", str(account.get("availablePoint", "数据缺失")))]
    if a.get("addressId"):
        rows.append(("收货地址 ID", str(a["addressId"])))
    return Card("确认积分商城兑换", rows, [
        "所需积分与现金以下单返回为准，可能同时需要支付现金",
        "下单后为「待支付」状态，需在官方页面完成支付；下单成功 ≠ 兑换成功"])


async def _party_order(a: dict) -> Card:
    labels = (("活动 spuId", "spuId"), ("规格 skuId", "skuId"), ("城市 code", "code"), ("门店", "storeCode"),
              ("日期", "dateStr"), ("开始", "timeStart"), ("结束", "timeEnd"), ("人数", "count"), ("场次余位", "leftNum"))
    rows = [("类型", {1: "包场", 2: "拼团"}.get(a.get("partyType"), str(a.get("partyType"))))]
    rows += [(k, str(a[f])) for k, f in labels if a.get(f) is not None]
    return Card("确认主题活动预约", rows, ["价格以下单返回为准，需在官方支付页完成支付"])


async def _auto_bind(a: dict) -> Card:
    return Card("确认一键领券", [("操作", "领取麦麦省当前所有可领的优惠券"), ("费用", "领取不花钱、不扣积分")])


async def _create_address(a: dict) -> Card:
    if not re.fullmatch(r"1\d{10}", str(a.get("phone", ""))):
        raise Refuse("手机号必须是 11 位纯数字，请向用户确认")
    return Card("确认新增收货地址", [
        ("联系人", f"{a['contactName']} {a.get('gender', '')}".strip()), ("手机号", a["phone"]),
        ("地址", f"{a['city']} {a['address']} {a['addressDetail']}")])


CARDS = {"create-order": _create_order, "cancel-order": _cancel_order, "draw-lottery": _draw_lottery,
         "mall-create-order": _mall_create_order, "party-order-create": _party_order,
         "auto-bind-coupons": _auto_bind, "delivery-create-address": _create_address}


def fingerprint(name: str, args: dict, card: Card) -> str:
    raw = json.dumps({"tool": name, "args": args, "rows": card.rows, "facts": card.facts},
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _out(status: str, message: str, result: Any = None) -> str:
    body: dict[str, Any] = {"status": status, "message": message}
    if result is not None:
        body["result"] = result
    return json.dumps(body, ensure_ascii=False)


async def run_write(name: str, args: dict, call_id: str) -> str:
    """执行一个写工具，返回交给模型的结果文本。call_id 是 tool_call_id，作为台账幂等键。"""
    settings = await asyncio.to_thread(client.load_settings)
    account = client.account_key(settings)
    ledger = await asyncio.to_thread(Ledger)
    # 1. 同一次工具调用已经执行过（崩溃后重放）：只返回记录，绝不重发
    if done := await asyncio.to_thread(ledger.get, call_id):
        return _out(done["status"], "这次操作此前已执行过，以下是台账中的记录，不会重复提交", done["result"])
    # 2. 冻结时直接拒绝，免得用户确认了一张注定无法执行的卡
    if pending := await asyncio.to_thread(ledger.pending, account):
        return _out("FROZEN", f"账户有一笔结果未知的操作（{pending}），已冻结所有写操作。"
                              "请用户先在麦当劳 App 核实，再由本人执行台账命令解冻。")
    try:
        validate(args, client.tool_schemas()[name]["input_schema"])
        card = await CARDS[name](args)
    except ValidationError as e:
        return _out("INVALID", f"参数不符合要求：{e.message}")
    except Refuse as e:
        return _out("REFUSED", str(e))

    fp = fingerprint(name, args, card)
    answer = interrupt({"type": "mcd_confirm", "tool": name, "title": card.title,
                        "rows": card.rows, "warnings": card.warnings, "fingerprint": fp})
    if not (isinstance(answer, dict) and answer.get("confirmed") is True):
        return _out("CANCELLED", "用户取消：什么都没执行，积分和金额没有变动")
    if answer.get("fingerprint") != fp:
        return _out("CHANGED", "确认期间实时信息发生了变化（如价格、消耗或订单状态），本次未执行。"
                               "请把最新情况告诉用户，由用户重新发起")

    # 3. 认领（原子）→ 发请求 → 结算
    try:
        if done := await asyncio.to_thread(ledger.begin, call_id, account, name, args, fp):
            return _out(done["status"], "这次操作此前已执行过，不会重复提交", done["result"])
    except Frozen as e:
        return _out("FROZEN", str(e))
    try:
        payload = await client.call(name, args, writing=True)
    except McdUnknown as e:
        await asyncio.to_thread(ledger.settle, call_id, "UNKNOWN", {"error": str(e)})
        return _out("UNKNOWN", f"{e} 台账记录号：{call_id}")
    except McdError as e:  # 请求发出前失败（连接、配置、参数）：确定未执行
        await asyncio.to_thread(ledger.settle, call_id, "FAILED", {"error": str(e)})
        return _out("FAILED", f"未执行：{e}")
    if err := business_error(payload):
        await asyncio.to_thread(ledger.settle, call_id, "FAILED", payload)
        return _out("FAILED", f"服务端拒绝（原样转述）：{err}", data_of(payload))
    await asyncio.to_thread(ledger.settle, call_id, "SUCCESS", payload)
    return _out("SUCCESS", "已执行一次", data_of(payload))
