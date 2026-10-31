"""麦当劳连接器：官方 MCP 的读写白名单、写操作确认卡、返回值处理。

通用流程（调用、确认、台账）在 cooker/connectors/；这里只放麦当劳特有的部分。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cooker.connectors.actions import read
from cooker.connectors.base import Card, Connector, Refuse, data_of

# 有副作用：扣积分 / 花钱 / 改账户数据。必须经确认卡 + 台账执行
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

BE_TYPES = {1: "到店取餐", 2: "麦乐送", 5: "得来速", 6: "企业团餐"}
CANCEL_REASONS = {"1": "改主意了", "2": "重复下单", "3": "点错了/点多了/点少了",
                  "4": "地址/电话填错了", "5": "送达时间选错了", "-1": "其它原因"}
PRICE_KEYS = ("storeCode", "beCode", "orderType", "beType", "reservationDate", "gmServiceCode",
              "withOrder", "needTableware", "items")
# calculate-price 的金额字段单位是「分」，create-order 用「元」，混用差 100 倍 —— 由代码换算，不让模型心算
FEN_FIELDS = ("productOriginalPrice", "productPrice", "deliveryOriginalPrice", "deliveryPrice",
              "originalPrice", "discount", "price", "tablewarePrice")


def yuan(fen: Any) -> str | None:
    try:
        return f"{int(fen) / 100:.2f}"
    except (TypeError, ValueError):
        return None


def _money(fen: Any) -> str:
    v = yuan(fen)
    return f"¥{v}" if v is not None else "数据缺失"


def present(name: str, payload: Any) -> Any:
    """算价结果附上「元」的换算，模型直接引用。"""
    data = data_of(payload)
    if name != "calculate-price" or not isinstance(data, dict):
        return payload
    data = {**data, "金额换算（元，由代码按分÷100 计算）": {k: yuan(data[k]) for k in FEN_FIELDS if k in data}}
    return {**payload, "data": data} if isinstance(payload, dict) and "data" in payload else data


async def _create_order(conn: Connector, a: dict) -> Card:
    order_type = a.get("orderType")
    if order_type == 2 and not a.get("addressId"):
        raise Refuse("外送订单缺少 addressId：先用 delivery-query-addresses 让用户选择地址")
    if order_type == 1 and not a.get("takeWayCode"):
        raise Refuse("到店订单缺少 takeWayCode：取值只能来自 calculate-price 返回的 takeWayList")
    d = await read(conn, "calculate-price", {k: a[k] for k in PRICE_KEYS if k in a})
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


async def _cancel_order(conn: Connector, a: dict) -> Card:
    reason = str(a.get("cancelReasonCode"))
    if reason not in CANCEL_REASONS:
        raise Refuse(f"cancelReasonCode 只能取 {list(CANCEL_REASONS)}")
    d = await read(conn, "query-order", {"orderId": a["orderId"]})
    detail = d.get("orderDetail", d) if isinstance(d, dict) else {}
    status = detail.get("orderStatus", "数据缺失")
    warnings = [] if status == "待支付" else ["订单已不是「待支付」：能否取消、退款何时到账以麦当劳官方为准"]
    return Card("确认取消订单", [
        ("订单号", str(a["orderId"])), ("当前状态", str(status)),
        ("门店", str(detail.get("storeName", "数据缺失"))),
        ("实付", f"¥{detail['realTotalAmount']}" if detail.get("realTotalAmount") else "数据缺失"),
        ("取消原因", CANCEL_REASONS[reason])], warnings, facts={"status": status})


async def _draw_lottery(conn: Connector, a: dict) -> Card:
    d = await read(conn, "query-lottery-info")
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


async def _mall_create_order(conn: Connector, a: dict) -> Card:
    category = str(a.get("spuCategory"))
    if category not in ("1", "2"):
        raise Refuse('spuCategory 只能是 "1"（虚拟）或 "2"（实物），取自商品详情')
    if category == "2" and not a.get("addressId"):
        raise Refuse("实物商品必须先有收货地址：用 delivery-query-addresses 让用户选择，不能先下单后补地址")
    account = await read(conn, "query-my-account")
    rows = [("商品 SKU", str(a["skuId"])), ("数量", str(a.get("count", 1))),
            ("类型", "实物（寄送到地址）" if category == "2" else "虚拟商品/券"),
            ("当前可用积分", str(account.get("availablePoint", "数据缺失")))]
    if a.get("addressId"):
        rows.append(("收货地址 ID", str(a["addressId"])))
    return Card("确认积分商城兑换", rows, [
        "所需积分与现金以下单返回为准，可能同时需要支付现金",
        "下单后为「待支付」状态，需在官方页面完成支付；下单成功 ≠ 兑换成功"])


async def _party_order(conn: Connector, a: dict) -> Card:
    labels = (("活动 spuId", "spuId"), ("规格 skuId", "skuId"), ("城市 code", "code"), ("门店", "storeCode"),
              ("日期", "dateStr"), ("开始", "timeStart"), ("结束", "timeEnd"), ("人数", "count"), ("场次余位", "leftNum"))
    rows = [("类型", {1: "包场", 2: "拼团"}.get(a.get("partyType"), str(a.get("partyType"))))]
    rows += [(k, str(a[f])) for k, f in labels if a.get(f) is not None]
    return Card("确认主题活动预约", rows, ["价格以下单返回为准，需在官方支付页完成支付"])


async def _auto_bind(conn: Connector, a: dict) -> Card:
    return Card("确认一键领券", [("操作", "领取麦麦省当前所有可领的优惠券"), ("费用", "领取不花钱、不扣积分")])


async def _create_address(conn: Connector, a: dict) -> Card:
    if not re.fullmatch(r"1\d{10}", str(a.get("phone", ""))):
        raise Refuse("手机号必须是 11 位纯数字，请向用户确认")
    return Card("确认新增收货地址", [
        ("联系人", f"{a['contactName']} {a.get('gender', '')}".strip()), ("手机号", a["phone"]),
        ("地址", f"{a['city']} {a['address']} {a['addressDetail']}")])


CONNECTOR = Connector(
    key="mcd", label="麦当劳", skill="mclottery-alchemist", url="https://mcp.mcd.cn",
    env_prefix="MCD_MCP_", env_file="config/mcd-mcp.local.env", package_dir=Path(__file__).parent,
    read_tools=READ_TOOLS, write_tools=WRITE_TOOLS, app_hint="麦当劳 App",
    cards={"create-order": _create_order, "cancel-order": _cancel_order, "draw-lottery": _draw_lottery,
           "mall-create-order": _mall_create_order, "party-order-create": _party_order,
           "auto-bind-coupons": _auto_bind, "delivery-create-address": _create_address},
    present=present,
)
