"""从工具返回中提取商品图片，作为 ToolMessage.artifact 交给界面（模型看不到，不占 token）。

为什么不让模型输出图片：地址是确定性数据，模型抄写可能截断或编造；而且返回里的图片地址要过白名单。
规则：在返回结构中递归查找「同一个对象里既有名称字段、又有图片字段」的条目；
图片必须是 https 且域名属于连接器声明的官方图床（防止返回内容被用来加载任意外部地址，如追踪像素）。

2026-10-31 实测的字段：瑞幸 pictureUrl / breviaryPicUrl / bigPicUrl（luckincoffeecdn.com）；
麦当劳菜单 image（menu-img.mcd.cn）、奖品 imageUrl、券 couponImage（img.mcd.cn）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

IMAGE_KEYS = ("pictureUrl", "breviaryPicUrl", "bigPicUrl", "imageUrl", "image", "couponImage")
NAME_KEYS = ("productName", "name", "couponName", "spuName", "title")
MAX_IMAGES = 80  # 麦当劳整份菜单实测超过 30 个餐品；只存进 artifact，不发给模型


def allowed(url: Any, hosts: frozenset[str]) -> bool:
    if not isinstance(url, str):
        return False
    u = urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in hosts)


def extract_images(payload: Any, hosts: frozenset[str]) -> list[dict]:
    found: dict[str, dict] = {}  # 按地址去重，保持出现顺序

    def walk(v: Any) -> None:
        if len(found) >= MAX_IMAGES:
            return
        if isinstance(v, dict):
            name = next((v[k] for k in NAME_KEYS if isinstance(v.get(k), str) and v[k].strip()), None)
            url = next((v[k] for k in IMAGE_KEYS if allowed(v.get(k), hosts)), None)
            if name and url and url not in found:
                found[url] = {"name": name.strip(), "url": url}
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)

    if hosts:
        walk(payload)
    return list(found.values())


def strip_images(payload: Any) -> Any:
    """从交给模型的结果里去掉图片地址：图片由界面按 artifact 展示，模型不需要它们（省 token，也避免模型自己输出图片）。
    只去掉 IMAGE_KEYS 中的字段；支付二维码 payOrderQrCodeUrl 等业务字段不受影响。"""
    if isinstance(payload, dict):
        return {k: strip_images(v) for k, v in payload.items()
                if not (k in IMAGE_KEYS and isinstance(v, str) and v.startswith("http"))}
    if isinstance(payload, list):
        return [strip_images(v) for v in payload]
    return payload
