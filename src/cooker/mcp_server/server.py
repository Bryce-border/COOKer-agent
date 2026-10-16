"""COOKer MCP Server：把菜谱能力以 MCP 工具暴露，供本项目 Agent 及 Claude Desktop / Cursor 等客户端使用。

工具只做参数整理与错误翻译，业务逻辑全部在 cooker.service。
工具描述（docstring）会原样发给 LLM，决定模型「何时调用哪个工具」，因此写清用途和区别。

运行：
    cooker-mcp                    # stdio（桌面客户端默认方式）
    cooker-mcp --http --port 8765 # Streamable HTTP
"""

from __future__ import annotations

import argparse
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from cooker.recipes.recommend import IngredientMatch
from cooker.recipes.scaling import ScaledRecipe, ShoppingList
from cooker.recipes.store import RecipeFilter, RecipeNotFound
from cooker.service import RecipeDetail, RecipeSummary, SearchResult, TipResult, get_service

mcp = FastMCP(
    name="cooker",
    instructions=(
        "HowToCook 菜谱库（372 道家常菜 + 烹饪技巧）。回答做法时以 get_recipe 返回的原文为准；"
        "用量换算和购物清单使用 scale_recipe / make_shopping_list 的计算结果，不要自行心算。"
    ),
)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}

Category = Annotated[
    list[str] | None,
    Field(description="分类，可选：素菜 荤菜 水产 早餐 主食 半成品加工 汤与粥 饮料 酱料和其它材料 甜品"),
]
Allergens = Annotated[list[str] | None, Field(description="排除过敏原：花生 坚果 乳制品 蛋 海鲜 鱼 大豆 麸质 芝麻")]


def _filter(categories=None, max_difficulty=None, vegetarian=None, exclude_allergens=None,
            exclude_ingredients=None, max_calories=None, max_time_minutes=None) -> RecipeFilter | None:
    flt = RecipeFilter(
        categories=categories or None, max_difficulty=max_difficulty, vegetarian=vegetarian,
        exclude_allergens=exclude_allergens or [], exclude_ingredients=exclude_ingredients or [],
        max_calories=max_calories, max_time_minutes=max_time_minutes,
    )
    return flt if flt.model_dump(exclude_defaults=True) else None


def _call(fn, *args, **kwargs):
    """把领域异常翻译为 ToolError：模型能看到可读的错误并据此改正参数，而不是收到堆栈。"""
    try:
        return fn(*args, **kwargs)
    except RecipeNotFound as e:
        raise ToolError(str(e)) from e
    except (ValueError, RuntimeError) as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=READ_ONLY)
def search_recipes(
    query: Annotated[str, Field(description="自然语言需求，如「下饭的辣菜」「适合减脂的早餐」「红烧肉」")],
    categories: Category = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5, description="最高难度 1-5")] = None,
    vegetarian: Annotated[bool | None, Field(description="true 只要素食")] = None,
    exclude_allergens: Allergens = None,
    exclude_ingredients: Annotated[list[str] | None, Field(description="忌口食材，如 香菜")] = None,
    max_calories: Annotated[int | None, Field(description="最高卡路里（大卡）")] = None,
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[SearchResult]:
    """按描述语义搜索菜谱（关键词 + 向量混合检索，并重排）。返回菜谱摘要与命中片段。
    手头有哪些食材、想知道能做什么时，用 recommend_by_ingredients 而不是本工具。"""
    flt = _filter(categories, max_difficulty, vegetarian, exclude_allergens, exclude_ingredients, max_calories)
    return _call(get_service().search_recipes, query, flt, limit)


@mcp.tool(annotations=READ_ONLY)
def get_recipe(name: Annotated[str, Field(description="菜名或菜谱 ID，如「可乐鸡翅」或 meat_dish/可乐鸡翅")]) -> RecipeDetail:
    """获取一道菜的完整做法：原料、用量计算、分步操作、附加说明。找不到时会给出相近菜名。"""
    return _call(get_service().get_recipe, name)


@mcp.tool(annotations=READ_ONLY)
def recommend_by_ingredients(
    have: Annotated[list[str], Field(description="手头已有的食材，如 [\"土豆\", \"鸡蛋\", \"番茄\"]")],
    categories: Category = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5)] = None,
    vegetarian: bool | None = None,
    exclude_allergens: Allergens = None,
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> list[IngredientMatch]:
    """根据手头食材推荐能做的菜。按主料覆盖度排序，列出已匹配、缺少的主料和配料（常备调料不计入缺少）。"""
    flt = _filter(categories, max_difficulty, vegetarian, exclude_allergens)
    return _call(get_service().recommend, have, flt, limit)


@mcp.tool(annotations=READ_ONLY)
def browse_recipes(
    categories: Category = None,
    max_difficulty: Annotated[int | None, Field(ge=1, le=5)] = None,
    vegetarian: bool | None = None,
    exclude_allergens: Allergens = None,
    max_time_minutes: Annotated[int | None, Field(description="最长耗时（分钟，模型预估值）")] = None,
    random_pick: Annotated[bool, Field(description="true 随机挑选，用于「今天吃什么」")] = False,
    limit: Annotated[int, Field(ge=1, le=20)] = 10,
) -> list[RecipeSummary]:
    """按条件筛选菜谱（无需描述）。random_pick=true 时随机抽取，适合帮用户决定吃什么。"""
    flt = _filter(categories, max_difficulty, vegetarian, exclude_allergens, max_time_minutes=max_time_minutes)
    return _call(get_service().browse, flt, limit, random_pick)


@mcp.tool(annotations=READ_ONLY)
def scale_recipe(
    name: Annotated[str, Field(description="菜名或菜谱 ID")],
    people: Annotated[int, Field(ge=1, le=100, description="用餐人数")],
) -> ScaledRecipe:
    """把一道菜的原料用量换算到指定人数（程序精确计算）。"""
    return _call(get_service().scale, name, people)


class DishOrder(BaseModel):
    name: str = Field(description="菜名或菜谱 ID")
    people: int = Field(ge=1, le=100, description="这道菜的用餐人数")


@mcp.tool(annotations=READ_ONLY)
def make_shopping_list(dishes: Annotated[list[DishOrder], Field(min_length=1, max_length=30)]) -> ShoppingList:
    """为多道菜生成合并后的购物清单：同名食材自动相加，调料单独归类。"""
    return _call(get_service().shopping, [(d.name, d.people) for d in dishes])


@mcp.tool(annotations=READ_ONLY)
def search_cooking_tips(
    query: Annotated[str, Field(description="烹饪技巧问题，如「油温怎么判断」「焯水要多久」「怎么去腥」")],
    limit: Annotated[int, Field(ge=1, le=5)] = 3,
) -> list[TipResult]:
    """搜索烹饪技巧文章（油温、焯水、腌制、去腥、厨具使用、食品安全等通用知识）。"""
    return _call(get_service().search_tips, query, limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="COOKer MCP Server")
    parser.add_argument("--http", action="store_true", help="使用 Streamable HTTP 而不是 stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    get_service()  # 启动时预加载，首个请求不用等待；数据缺失也能尽早报错
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
