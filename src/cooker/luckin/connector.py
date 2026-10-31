"""瑞幸连接器：官方 MCP（my-coffee）的读写白名单、写操作确认卡、返回值处理。

技能（skills/my-coffee/SKILL.md）的硬约束里，能用代码保证的都在这里保证，不只靠提示词：
- 价格以 previewOrder 为准 → 下单确认卡由实时 previewOrder 生成；
- previewOrder 返回的 couponCodeList 必须原样透传 → 由代码写进最终参数（模型传错或漏传都会被覆盖）；
- createOrder 使用门店坐标 → 由代码取 previewOrder.shopInfo 的坐标；
- 预览明细必须与下单一致 → 不一致直接拒绝，不弹卡；
- 禁止展示 payOrderUrl → 交给模型前删除。
2026-10-31 实测：金额字段单位是「元」（如 20.0）；门店 workStatus 例如「打烊中」。
2026-11-01 据官方工具文档补充：订单状态码、orderPayAmount、19 位数字 orderId（另有字符串 orderIdStr）、needPay。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import Field

from cooker.connectors.actions import read
from cooker.connectors.base import Card, Connector, Refuse
from cooker.connectors.images import extract_images

WRITE_TOOLS = frozenset({"createOrder", "cancelOrder"})
# switchProduct 只是按属性换算出新的 SKU，previewOrder 只预览不建单，都没有副作用
READ_TOOLS = frozenset({"queryShopList", "searchProductForMcp", "switchProduct", "queryProductDetailInfo",
                        "previewOrder", "queryOrderDetailInfo"})
HIDDEN_FIELDS = ("payOrderUrl",)  # 技能要求只展示 payOrderQrCodeUrl
# queryOrderDetailInfo.orderStatus（官方文档）
ORDER_STATUS = {10: "待付款", 20: "下单成功", 30: "制作中", 60: "等待取餐", 80: "已完成", 100: "已取消"}


def _money(v: Any) -> str:
    try:
        return f"¥{float(v):.2f}"
    except (TypeError, ValueError):
        return "数据缺失"


def _line_key(p: dict) -> tuple:
    return (p.get("productId"), str(p.get("skuCode")), p.get("amount"))


async def _create_order(conn: Connector, a: dict) -> Card:
    preview = await read(conn, "previewOrder", {"deptId": a["deptId"], "productList": a["productList"]})
    if not isinstance(preview, dict) or preview.get("discountPrice") is None:
        raise Refuse("订单预览结果不完整（缺少应付金额），不能下单；请稍后重试或换个商品")
    items = preview.get("productInfoList") or []
    if sorted(map(_line_key, a["productList"])) != sorted(map(_line_key, items)):
        raise Refuse("预览返回的商品明细与下单参数不一致，未下单。请重新搜索商品后再试")
    shop = preview.get("shopInfo") or {}
    if shop.get("deptId") not in (None, a["deptId"]):
        raise Refuse("预览返回的门店与下单门店不一致，未下单")

    coupons = list(preview.get("couponCodeList") or [])
    final = {k: v for k, v in a.items() if k != "couponCodeList"}
    if coupons:
        final["couponCodeList"] = coupons  # 原样透传预览给的券码
    if shop.get("longitude") is not None and shop.get("latitude") is not None:
        final["longitude"], final["latitude"] = shop["longitude"], shop["latitude"]  # 门店坐标

    status = shop.get("workStatus")
    rows = [("门店", f"{shop.get('deptName', '数据缺失')}（{shop.get('address', '')}）".replace("（）", "")),
            ("取餐方式", "到店自取"),
            ("商品", "\n".join(f"{p.get('name')} × {p.get('amount')}（{p.get('additionDesc') or '默认规格'}）"
                               for p in items)),
            ("原价", _money(preview.get("totalInitialPrice"))),
            ("减免", _money(preview.get("privilegeMoney"))),
            ("应付", _money(preview.get("discountPrice"))),
            ("优惠券", f"自动使用 {len(coupons)} 张" if coupons else "无")]
    if a.get("remark"):
        rows.append(("备注", str(a["remark"])))
    warnings = ["金额来自刚刚的实时预览；确认后生成支付二维码，需在瑞幸支付页完成支付，COOKer 不代付"]
    if status and status != "营业中":
        warnings.append(f"门店当前状态为「{status}」，下单可能失败或无法按时取餐")
    if a.get("couponCodeList") and a["couponCodeList"] != coupons:
        warnings.append("优惠券已按预览结果使用（与对话中传入的不同）")
    facts = {k: preview.get(k) for k in ("totalInitialPrice", "privilegeMoney", "discountPrice")}
    facts.update(coupons=coupons, items=sorted(map(_line_key, items)), status=status)
    images = extract_images(items, conn.image_hosts)  # 预览里的商品缩略图 breviaryPicUrl
    return Card("确认瑞幸下单", rows, warnings, facts=facts, args=final, images=images)


async def _cancel_order(conn: Connector, a: dict) -> Card:
    """字段名来自官方工具文档（尚未用真实订单核对）：orderStatus / orderStatusName / orderPayAmount / orderCommodityList。"""
    d = await read(conn, "queryOrderDetailInfo", {"orderId": a["orderId"]})
    d = d if isinstance(d, dict) else {}
    code = d.get("orderStatus")
    status = d.get("orderStatusName") or ORDER_STATUS.get(code) or "数据缺失"
    if code in (80, 100):
        raise Refuse(f"订单当前状态为「{status}」，不能取消")
    goods = [f"{c.get('commodityName')} ¥{c.get('payMoney')}" for c in d.get("orderCommodityList") or []]
    goods = goods or [f"{p.get('name')} × {p.get('amount')}" for p in d.get("productInfoList") or []]
    rows = [("订单号", str(a["orderId"])), ("当前状态", str(status)),
            ("门店", str((d.get("shopInfo") or {}).get("deptName") or "数据缺失")),
            ("商品", "\n".join(goods) or "数据缺失"),
            ("金额", _money(d.get("orderPayAmount")))]
    warnings = ["已支付订单能否取消、退款何时到账以瑞幸官方为准"]
    if code in (30, 60):
        warnings.insert(0, f"订单已「{status}」，门店可能无法取消")
    return Card("确认取消瑞幸订单", rows, warnings, facts={"status": code, "name": status})


def _drop_hidden(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_hidden(v) for k, v in value.items() if k not in HIDDEN_FIELDS}
    if isinstance(value, list):
        return [_drop_hidden(v) for v in value]
    return value


def _string_order_id(payload: Any) -> Any:
    """createOrder 的 orderId 是 19 位数字，超出 JavaScript 安全整数（2^53），前端解析会丢精度；改用官方给的 orderIdStr。"""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("orderIdStr"):
        return {**payload, "data": {**data, "orderId": str(data["orderIdStr"])}}
    return payload


def present(name: str, payload: Any) -> Any:
    if name == "createOrder":
        payload = _string_order_id(payload)
    return _drop_hidden(payload) if name in ("createOrder", "queryOrderDetailInfo") else payload


def _local_tools(conn: Connector) -> list[BaseTool]:
    """瑞幸没有「订单列表」接口：通过 COOKer 下的单都记在台账里，从台账读最近订单号（只读，不调接口）。"""
    from cooker.connectors import actions, client
    from cooker.connectors.tools import skill_guard

    @tool  # 同步函数：读本地 SQLite，CLI（invoke）与 Server（ainvoke，放进线程池）都能用
    def luckin_recent_orders(runtime: ToolRuntime,
                                   limit: Annotated[int, Field(ge=1, le=10)] = 5) -> str:
        """列出最近通过 COOKer 创建的瑞幸订单（订单号、下单时间、应付金额），用于「看看最近的订单」。
        只包含本机经 COOKer 下的单，不含瑞幸 App 里的订单；查看状态与取餐码请再用 queryOrderDetailInfo。"""
        if refused := skill_guard(conn, runtime):
            return refused
        # 与 run_write 用同一个台账入口（actions.Ledger），写入与读取一定是同一个文件
        rows = actions.Ledger().history(client.account_key(conn, client.load_credentials(conn)), "createOrder", limit)
        orders = []
        for r in rows:
            data = (r["result"] or {}).get("data") or {}
            orders.append({"orderId": str(data.get("orderIdStr") or data.get("orderId") or ""),
                           "下单时间": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created"])),
                           "应付": _money(data.get("discountPrice"))})
        note = "仅含本机经 COOKer 下的单" if orders else "本机还没有经 COOKer 下过瑞幸订单；瑞幸 App 里的订单请提供订单号查询"
        return json.dumps({"orders": orders, "note": note}, ensure_ascii=False)

    return [luckin_recent_orders]


CONNECTOR = Connector(
    key="luckin", label="瑞幸", skill="my-coffee", url="https://gwmcp.lkcoffee.com/order/user/mcp",
    env_prefix="LUCKIN_MCP_", env_file="config/luckin-mcp.local.env", package_dir=Path(__file__).parent,
    read_tools=READ_TOOLS, write_tools=WRITE_TOOLS, app_hint="瑞幸咖啡 App",
    image_hosts=frozenset({"luckincoffeecdn.com"}),
    cards={"createOrder": _create_order, "cancelOrder": _cancel_order},
    present=present, local_tools=_local_tools,
)
