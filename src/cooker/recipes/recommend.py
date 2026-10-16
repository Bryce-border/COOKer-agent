"""按现有食材推荐：基于结构化 main_ingredients 的覆盖度打分，不走向量检索。

「我有土豆和鸡蛋」是集合匹配问题，不是语义相似问题：向量检索会把「土豆烧牛肉」和
「土豆泥」打成相近分数，却不知道你缺不缺牛肉。
"""

from __future__ import annotations

from pydantic import BaseModel

from cooker.ingest.schema import Recipe
from cooker.recipes.ingredients import is_pantry, matches, normalize
from cooker.recipes.store import RecipeFilter, RecipeStore


class IngredientMatch(BaseModel):
    recipe_id: str
    name: str
    difficulty: int
    matched: list[str]
    missing_main: list[str]
    missing_other: list[str]
    coverage: float


def _main(recipe: Recipe) -> list[str]:
    # LLM 给的主料可能混入调料（如 生抽），评分时去掉
    names = [normalize(n) for n in recipe.facts.main_ingredients if not is_pantry(n)]
    return list(dict.fromkeys(names)) or [normalize(recipe.facts.ingredients[0].name)]


def recommend_by_ingredients(
    store: RecipeStore,
    have: list[str],
    flt: RecipeFilter | None = None,
    limit: int = 5,
    max_missing_main: int = 1,
) -> list[IngredientMatch]:
    have = [h for h in (x.strip() for x in have) if h]
    if not have:
        raise ValueError("至少提供一种食材")

    results = []
    for r in store.filter(flt):
        if r.facts is None:
            continue
        main = _main(r)
        matched = [m for m in main if any(matches(h, m) for h in have)]
        if not matched:
            continue
        missing_main = [m for m in main if m not in matched]
        if len(missing_main) > max_missing_main:
            continue
        others = {normalize(i.name) for i in r.facts.ingredients if not i.optional} - set(main)
        missing_other = sorted(o for o in others if not is_pantry(o) and not any(matches(h, o) for h in have))
        results.append(
            IngredientMatch(
                recipe_id=r.id,
                name=r.name,
                difficulty=r.difficulty,
                matched=matched,
                missing_main=missing_main,
                missing_other=missing_other,
                coverage=round(len(matched) / len(main), 2),
            )
        )
    # 缺主料少的优先 → 覆盖率高 → 用上的食材多 → 缺的配料少 → 简单的优先
    results.sort(key=lambda m: (len(m.missing_main), -m.coverage, -len(m.matched), len(m.missing_other), m.difficulty))
    return _diversify(results, have, limit)


def _diversify(ranked: list[IngredientMatch], have: list[str], limit: int) -> list[IngredientMatch]:
    """贪心重排：优先选能用上「尚未被已选菜覆盖」的食材的菜。

    单看每道菜的分数，「土豆 鸡蛋 番茄」的前 5 名可能全是鸡蛋菜；用户真正想要的是
    手头每样食材都有去处。思路同 MMR（最大边际相关）：兼顾相关性与结果集整体的覆盖。
    """
    picked: list[IngredientMatch] = []
    covered: set[str] = set()
    pool = list(ranked)
    while pool and len(picked) < limit:
        def gain(m: IngredientMatch) -> int:
            return sum(1 for h in have if h not in covered and any(matches(h, x) for x in m.matched))
        # max 在并列时返回第一个，即保留原排序作为次序依据
        best = max(pool, key=gain)
        if gain(best) == 0:
            best = pool[0]
        pool.remove(best)
        picked.append(best)
        covered |= {h for h in have if any(matches(h, x) for x in best.matched)}
    return picked
