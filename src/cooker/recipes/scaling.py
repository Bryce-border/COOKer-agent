"""按人数换算用量、合并购物清单。纯确定性计算，不调用 LLM。

为什么不让 LLM 算：乘除和单位合并是 LLM 的弱项（会算错、每次结果不同），
而 RecipeFacts 已经把用量结构化，代码计算可复现、可单测。
"""

from __future__ import annotations

import math
from collections import defaultdict

from pydantic import BaseModel

from cooker.ingest.schema import Ingredient, Recipe
from cooker.recipes.ingredients import is_pantry, normalize

_CONTINUOUS_UNITS = {"g", "ml"}


class ScaledItem(BaseModel):
    name: str
    amount: float | None
    amount_max: float | None
    unit: str | None
    scaling: str
    group: str | None = None
    optional: bool = False
    note: str | None = None

    def display(self) -> str:
        if self.amount is None:
            return f"{self.name} 适量"
        rng = f"{_fmt(self.amount)}-{_fmt(self.amount_max)}" if self.amount_max else _fmt(self.amount)
        return f"{self.name} {rng}{' ' + self.unit if self.unit else ''}"


class ScaledRecipe(BaseModel):
    recipe_id: str
    name: str
    people: int
    factor_note: str
    items: list[ScaledItem]
    warnings: list[str]


def _fmt(x: float | None) -> str:
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}".rstrip("0").rstrip(".")


def _round(amount: float | None, unit: str | None) -> float | None:
    """g/ml 保留到整数（<10 时保留 1 位小数）；个/根等计数单位向上取到 0.5，避免「0.35 个洋葱」。"""
    if amount is None:
        return None
    if unit in _CONTINUOUS_UNITS:
        return round(amount) if amount >= 10 else round(amount, 1)
    return math.ceil(amount * 2) / 2


def _scale_one(ing: Ingredient, people: int, servings: float) -> ScaledItem:
    factor = {"per_person": people, "per_serving": servings}.get(ing.scaling, 1)
    scale = (lambda v: _round(v * factor, ing.unit)) if ing.scaling in ("per_person", "per_serving") else (lambda v: v)
    return ScaledItem(
        name=ing.name,
        amount=scale(ing.amount) if ing.amount is not None else None,
        amount_max=scale(ing.amount_max) if ing.amount_max is not None else None,
        unit=ing.unit,
        scaling=ing.scaling,
        group=ing.group,
        optional=ing.optional,
        note=ing.note,
    )


def scale_recipe(recipe: Recipe, people: int) -> ScaledRecipe:
    if people < 1 or people > 100:
        raise ValueError("人数需在 1-100 之间")
    if recipe.facts is None:
        raise ValueError(f"「{recipe.name}」缺少结构化用量，无法自动换算，请参考原文「计算」一节")

    warnings: list[str] = []
    size = recipe.facts.serving_size_people
    if size is None:
        size = 1.0
        warnings.append("原文未说明一份够几人吃，按一份 1 人估算")
    servings = people / size
    items = [_scale_one(i, people, servings) for i in recipe.facts.ingredients]
    if any(i.scaling == "fixed" for i in items):
        warnings.append("标记为「固定用量」的原料（如一次性制作的酱汁基础量）未随人数变化，请按需调整")
    warnings.append("用量由模型从原文抽取后计算，请以原文「计算」一节为准")
    return ScaledRecipe(
        recipe_id=recipe.id,
        name=recipe.name,
        people=people,
        factor_note=f"原文一份约 {_fmt(size)} 人，{people} 人约 {_fmt(round(servings, 2))} 份",
        items=items,
        warnings=warnings,
    )


class ShoppingLine(BaseModel):
    name: str
    amount: str
    used_in: list[str]


class ShoppingList(BaseModel):
    groceries: list[ShoppingLine]
    pantry: list[ShoppingLine]
    warnings: list[str]


def shopping_list(scaled: list[ScaledRecipe]) -> ShoppingList:
    """合并多道菜：同名（归一化后）同单位的数量相加；单位不同则分别列出；适量的只记录出处。"""
    totals: dict[tuple[str, str | None], list[float]] = defaultdict(lambda: [0.0, 0.0])
    used_in: dict[str, list[str]] = defaultdict(list)
    to_taste: set[str] = set()
    order: list[str] = []

    for sr in scaled:
        for item in sr.items:
            if item.optional:
                continue
            name = normalize(item.name)
            if name not in used_in:
                order.append(name)
            if sr.name not in used_in[name]:
                used_in[name].append(sr.name)
            if item.amount is None:
                to_taste.add(name)
                continue
            t = totals[(name, item.unit)]
            t[0] += item.amount
            t[1] += item.amount_max if item.amount_max is not None else item.amount

    lines = {"groceries": [], "pantry": []}
    for name in order:
        parts = []
        for (n, unit), (lo, hi) in totals.items():
            if n != name:
                continue
            # 采购时计数单位只能买整的：5.5 个鸡蛋 → 6 个
            buy = (lambda v: _round(v, unit)) if unit in _CONTINUOUS_UNITS else math.ceil
            lo_r, hi_r = buy(lo), buy(hi)
            amount = _fmt(lo_r) if lo_r == hi_r else f"{_fmt(lo_r)}-{_fmt(hi_r)}"
            parts.append(f"{amount}{' ' + unit if unit else ''}")
        if name in to_taste and not parts:
            parts.append("适量")
        lines["pantry" if is_pantry(name) else "groceries"].append(
            ShoppingLine(name=name, amount=" + ".join(parts), used_in=used_in[name])
        )
    return ShoppingList(
        groceries=lines["groceries"],
        pantry=lines["pantry"],
        warnings=["已省略可选原料", "调料归入 pantry（多半家里已有）", "不同菜谱单位不同的同一食材分开列出（如 540 g + 1 个），未做换算"],
    )
