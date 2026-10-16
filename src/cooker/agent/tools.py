"""Agent 工具：进程内调用 CookerService。

与 MCP 工具的区别：这里能通过 ToolRuntime 读到当前用户档案，并把
「过敏原 / 忌口」强制合并到每次检索条件中——安全约束由代码保证，而不是靠模型记得传参。
工具返回 dict（ToolNode 会用 ensure_ascii=False 序列化成 JSON），出错时返回可读的错误说明，
让模型据此修正参数或如实告知用户，而不是让整个图崩溃。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from cooker.agent.profile import UserProfile, load_profile, save_profile
from cooker.ingest.schema import Allergen
from cooker.recipes.ingredients import matches
from cooker.recipes.store import RecipeFilter, RecipeNotFound
from cooker.service import get_service


@dataclass
class Context:
    """单次运行的上下文（LangGraph context_schema），由调用方传入。"""

    user_id: str = "local"
    provider: str | None = None  # 覆盖默认厂商：qwen / deepseek


def _ctx(runtime: ToolRuntime[Context]) -> Context:
    return runtime.context or Context()


def _profile(runtime: ToolRuntime[Context]) -> UserProfile:
    return load_profile(runtime.store, _ctx(runtime).user_id)


def _safe(fn):
    """领域异常 → 给模型看的错误字符串。其它异常继续抛出（由 ToolNode 兜底并记录）。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RecipeNotFound as e:
            return {"error": str(e), "suggestions": e.suggestions}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    return wrapper


def _filter(profile: UserProfile, **kw: Any) -> tuple[RecipeFilter, dict]:
    """合并档案约束。返回 (过滤条件, 本次自动应用的约束)，后者回传给模型以便在回答中说明。"""
    allergens = list(dict.fromkeys([*profile.allergies, *(kw.pop("exclude_allergens", None) or [])]))
    dislikes = list(dict.fromkeys([*profile.dislikes, *(kw.pop("exclude_ingredients", None) or [])]))
    if profile.vegetarian and kw.get("vegetarian") is None:
        kw["vegetarian"] = True
    flt = RecipeFilter(exclude_allergens=allergens, exclude_ingredients=dislikes,
                       **{k: v for k, v in kw.items() if v is not None})
    applied = {k: v for k, v in {"排除过敏原": profile.allergies, "排除忌口": profile.dislikes,
                                  "仅素食": profile.vegetarian}.items() if v}
    return flt, applied


def _dump(items) -> list[dict]:
    return [x.model_dump(mode="json", exclude_none=True) for x in items]


Categories = Annotated[
    list[str] | None, Field(description="分类：素菜 荤菜 水产 早餐 主食 半成品加工 汤与粥 饮料 酱料和其它材料 甜品")
]


@tool
@_safe
def search_recipes(
    query: Annotated[str, Field(description="自然语言需求，如「下饭的辣菜」「红烧肉」")],
    runtime: ToolRuntime[Context],
    categories: Categories = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5)] = None,
    max_calories: int | None = None,
    exclude_ingredients: Annotated[list[str] | None, Field(description="本次额外排除的食材")] = None,
    limit: Annotated[int, Field(ge=1, le=8)] = 5,
) -> dict:
    """按描述搜索菜谱（用于推荐、找相似的菜、确认库里有没有某道菜）。用户的过敏原与忌口会被自动排除，
    所以含过敏原的菜不会出现在结果里。
    用户点名问某道菜的做法、用量或能不能吃时，直接用 get_recipe，不要用本工具；
    用户报出手头食材时用 recommend_by_ingredients。"""
    flt, applied = _filter(_profile(runtime), categories=categories, max_difficulty=max_difficulty,
                           max_calories=max_calories, exclude_ingredients=exclude_ingredients)
    return {"results": _dump(get_service().search_recipes(query, flt, limit)), "auto_applied": applied}


@tool
@_safe
def get_recipe(name: Annotated[str, Field(description="菜名或菜谱 ID")], runtime: ToolRuntime[Context]) -> dict:
    """获取一道菜的完整做法（原料、用量、步骤、附加说明）。用户点名问某道菜时首选本工具，回答做法时以此为准。
    不受过敏原过滤，若与用户档案冲突会返回 profile_warning。"""
    detail = get_service().get_recipe(name)
    out = detail.model_dump(mode="json", exclude_none=True)
    profile = _profile(runtime)
    # 用户点名要某道菜时不拦截，但必须提醒冲突
    hit_allergens = sorted(set(profile.allergies) & set(detail.summary.allergens))
    hit_dislikes = [d for d in profile.dislikes if any(matches(d, i) for i in detail.ingredients)]
    if hit_allergens or hit_dislikes:
        out["profile_warning"] = (
            f"注意：这道菜可能含有用户的过敏原 {hit_allergens} / 忌口 {hit_dislikes}，回答时必须明确提醒"
        )
    return out


@tool
@_safe
def recommend_by_ingredients(
    have: Annotated[list[str], Field(description="手头已有的食材")],
    runtime: ToolRuntime[Context],
    categories: Categories = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5)] = None,
    limit: Annotated[int, Field(ge=1, le=8)] = 5,
) -> dict:
    """根据手头食材推荐能做的菜，列出缺少的主料和配料（常备调料不算缺少）。过敏原与忌口自动排除。"""
    flt, applied = _filter(_profile(runtime), categories=categories, max_difficulty=max_difficulty)
    return {"results": _dump(get_service().recommend(have, flt, limit)), "auto_applied": applied}


@tool
@_safe
def browse_recipes(
    runtime: ToolRuntime[Context],
    categories: Categories = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5)] = None,
    max_time_minutes: Annotated[int | None, Field(description="最长耗时（分钟，预估值）")] = None,
    random_pick: Annotated[bool, Field(description="true 随机挑选，用于「今天吃什么」")] = False,
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict:
    """按条件筛选菜谱，random_pick=true 时随机抽取。过敏原与忌口自动排除。"""
    flt, applied = _filter(_profile(runtime), categories=categories, max_difficulty=max_difficulty,
                           max_time_minutes=max_time_minutes)
    return {"results": _dump(get_service().browse(flt, limit, random_pick)), "auto_applied": applied}


@tool
@_safe
def scale_recipe(
    name: Annotated[str, Field(description="菜名或菜谱 ID")],
    runtime: ToolRuntime[Context],
    people: Annotated[int | None, Field(ge=1, le=100, description="用餐人数；不填则用档案中的常用人数")] = None,
) -> dict:
    """把「一道菜」的原料用量精确换算到指定人数。涉及具体用量时必须用它，不要心算。
    多道菜要合并成采购清单时用 make_shopping_list，不要多次调用本工具后自己相加。"""
    people = people or _profile(runtime).household_size
    if not people:
        return {"error": "需要用餐人数：请询问用户，或在档案中记录常用人数"}
    return get_service().scale(name, people).model_dump(mode="json", exclude_none=True)


class DishOrder(BaseModel):
    name: str = Field(description="菜名或菜谱 ID")
    people: int | None = Field(None, ge=1, le=100, description="不填则用档案中的常用人数")


@tool
@_safe
def make_shopping_list(
    dishes: Annotated[list[DishOrder], Field(min_length=1, max_length=20)], runtime: ToolRuntime[Context]
) -> dict:
    """为多道菜生成合并的购物清单：同名食材相加，调料单独归类。"""
    default = _profile(runtime).household_size
    items = []
    for d in dishes:
        if not (d.people or default):
            return {"error": f"「{d.name}」缺少用餐人数"}
        items.append((d.name, d.people or default))
    return get_service().shopping(items).model_dump(mode="json")


@tool
@_safe
def search_cooking_tips(query: Annotated[str, Field(description="烹饪技巧问题，如「油温怎么判断」")]) -> dict:
    """搜索烹饪技巧文章：油温、焯水、腌制、去腥、蒸煮、厨具使用、食品安全等。"""
    return {"results": _dump(get_service().search_tips(query, 3))}


@tool
def update_profile(
    runtime: ToolRuntime[Context],
    add_allergies: list[Allergen] | None = None,
    remove_allergies: list[Allergen] | None = None,
    add_dislikes: list[str] | None = None,
    remove_dislikes: list[str] | None = None,
    vegetarian: bool | None = None,
    household_size: Annotated[int | None, Field(ge=1, le=50)] = None,
    add_equipment: list[str] | None = None,
    skill: str | None = None,
) -> dict:
    """记录用户长期偏好（跨会话保留）。用户提到过敏、忌口、素食、家里几口人、有什么厨具、厨艺水平时调用。
    过敏原只能取：花生 坚果 乳制品 蛋 海鲜 鱼 大豆 麸质 芝麻；具体食材（如「虾」「香菜」）记为忌口。
    移除过敏原会先请用户确认。"""
    if runtime.store is None:
        return {"error": "当前运行环境未启用长期记忆，无法保存偏好"}
    ctx = _ctx(runtime)
    profile = load_profile(runtime.store, ctx.user_id)

    not_in_profile = [a for a in (remove_allergies or []) if a not in profile.allergies]
    removing = [a for a in (remove_allergies or []) if a in profile.allergies]
    if removing:
        # 人在回路：放宽安全约束必须由用户本人确认。
        # interrupt 会暂停整个图并把问题交给前端；恢复时本工具从头重新执行，
        # 所以 interrupt 之前不能有任何写操作（这里只读取了档案）。
        answer = interrupt({"type": "confirm", "question": f"确认从档案中移除过敏原「{'、'.join(removing)}」吗？"
                            "移除后推荐将不再排除含这些成分的菜。"})
        confirmed = str(answer).strip().lower() in {"是", "确认", "y", "yes", "true", "1"}
        if not confirmed:
            removing = []

    p = profile.model_copy(deep=True)
    p.allergies = [a for a in dict.fromkeys([*p.allergies, *(add_allergies or [])]) if a not in removing]
    p.dislikes = [d for d in dict.fromkeys([*p.dislikes, *(add_dislikes or [])]) if d not in (remove_dislikes or [])]
    p.equipment = list(dict.fromkeys([*p.equipment, *(add_equipment or [])]))
    if vegetarian is not None:
        p.vegetarian = vegetarian
    if household_size is not None:
        p.household_size = household_size
    if skill in ("新手", "有经验", "熟练"):
        p.skill = skill
    save_profile(runtime.store, ctx.user_id, p)
    result: dict[str, Any] = {"saved": True, "profile": p.describe()}
    notes = []
    if not_in_profile:
        notes.append(f"档案中本来就没有过敏原 {not_in_profile}，无需移除")
    if remove_allergies and len(not_in_profile) < len(remove_allergies) and not removing:
        notes.append("用户未确认，过敏原未移除")
    if notes:
        result["note"] = "；".join(notes)
    return result


@tool
def get_profile(runtime: ToolRuntime[Context]) -> dict:
    """查看已记录的用户偏好。"""
    return {"profile": _profile(runtime).describe()}


TOOLS = [search_recipes, get_recipe, recommend_by_ingredients, browse_recipes, scale_recipe,
         make_shopping_list, search_cooking_tips, update_profile, get_profile]
