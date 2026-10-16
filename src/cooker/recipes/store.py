"""菜谱存储：把 data/recipes.json、tips.json 加载到内存，提供按 ID / 名称查找与结构化过滤。

372 道菜全部常驻内存（约 4MB），过滤就是一次列表遍历；没有引入数据库的必要。
"""

from __future__ import annotations

import difflib
import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from cooker.ingest.schema import ParsedTip, Recipe
from cooker.recipes.ingredients import dish_key, matches
from cooker.settings import get_settings


class RecipeFilter(BaseModel):
    """所有条件取交集；为 None / 空表示不限制。"""

    categories: list[str] | None = Field(None, description="分类目录名或中文名，如 meat_dish / 荤菜")
    max_difficulty: int | None = Field(None, ge=1, le=5)
    max_calories: int | None = None
    max_time_minutes: int | None = None
    vegetarian: bool | None = None
    exclude_allergens: list[str] = Field(default_factory=list)
    exclude_ingredients: list[str] = Field(default_factory=list, description="忌口食材，如 香菜、猪肉")
    flavors: list[str] = Field(default_factory=list, description="需包含任一口味")
    methods: list[str] = Field(default_factory=list, description="需包含任一烹饪方式")

    def accepts(self, r: Recipe) -> bool:
        f = r.facts
        if self.categories and r.category not in self.categories and r.category_name not in self.categories:
            return False
        if self.max_difficulty and r.difficulty > self.max_difficulty:
            return False
        if self.max_calories and (r.calories is None or r.calories > self.max_calories):
            return False
        if self.exclude_allergens and set(self.exclude_allergens) & set(r.allergens):
            return False
        if self.exclude_ingredients:
            names = [*(i.name for i in f.ingredients), *r.ingredients_raw] if f else r.ingredients_raw
            if any(matches(x, n) for x in self.exclude_ingredients for n in names):
                return False
        if f is None:
            # 没有 facts 时，依赖 facts 的条件一律视为不满足，宁缺毋滥
            return not (self.vegetarian is not None or self.flavors or self.methods or self.max_time_minutes)
        if self.vegetarian is not None and f.is_vegetarian != self.vegetarian:
            return False
        if self.max_time_minutes and (f.total_time_minutes is None or f.total_time_minutes > self.max_time_minutes):
            return False
        if self.flavors and not set(self.flavors) & set(f.flavors):
            return False
        if self.methods and not set(self.methods) & set(f.methods):
            return False
        return True


class RecipeNotFound(LookupError):
    def __init__(self, query: str, suggestions: list[str]):
        self.query, self.suggestions = query, suggestions
        hint = f"，你是不是想找：{'、'.join(suggestions)}" if suggestions else ""
        super().__init__(f"没有找到菜谱「{query}」{hint}")


class RecipeStore:
    def __init__(self, recipes: list[Recipe], tips: list[ParsedTip]):
        self.recipes = recipes
        self.tips = tips
        self._by_id = {r.id: r for r in recipes}
        self._by_name = {r.name: r for r in recipes}
        self._by_key = {dish_key(r.name): r for r in recipes}
        self._tips_by_id = {t.id: t for t in tips}

    @classmethod
    def load(cls, data_dir: Path | None = None) -> RecipeStore:
        data_dir = data_dir or get_settings().data_dir
        path = data_dir / "recipes.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} 不存在，请先运行 cooker-ingest")
        recipes = [Recipe.model_validate(x) for x in json.loads(path.read_text(encoding="utf-8"))]
        tips = [ParsedTip.model_validate(x) for x in json.loads((data_dir / "tips.json").read_text(encoding="utf-8"))]
        return cls(recipes, tips)

    def get(self, recipe_id: str) -> Recipe | None:
        return self._by_id.get(recipe_id)

    def get_tip(self, tip_id: str):
        return self._tips_by_id.get(tip_id)

    def find(self, name_or_id: str) -> Recipe:
        """ID / 菜名精确 → 同义归一后精确 → 归一后唯一包含匹配；否则抛 RecipeNotFound 并给出候选。"""
        q = name_or_id.strip().removesuffix("的做法")
        if r := self._by_id.get(q) or self._by_name.get(q) or self._by_key.get(dish_key(q)):
            return r
        k = dish_key(q)
        contains = [r for key, r in self._by_key.items() if k and (k in key or key in k)]
        if len(contains) == 1:
            return contains[0]
        suggestions = [r.name for r in contains[:5]] or difflib.get_close_matches(q, self._by_name, n=5, cutoff=0.4)
        raise RecipeNotFound(name_or_id, suggestions)

    def filter(self, flt: RecipeFilter | None = None) -> list[Recipe]:
        return [r for r in self.recipes if flt is None or flt.accepts(r)]


@lru_cache
def get_store() -> RecipeStore:
    return RecipeStore.load()
