"""业务服务层：组合 store / retriever / 领域函数，输出面向调用方（MCP、Agent、API）的精简模型。

MCP 工具和 P2 的 LangGraph Agent 都调用这里，而不是各自拼装底层组件。
返回值刻意精简（不带 markdown 全文），因为它们会进入 LLM 上下文，字段越多 token 越贵、干扰越大。
"""

from __future__ import annotations

import random
from functools import lru_cache

from pydantic import BaseModel

from cooker.ingest.schema import Recipe
from cooker.recipes.recommend import IngredientMatch, recommend_by_ingredients
from cooker.recipes.scaling import ScaledRecipe, ShoppingList, scale_recipe, shopping_list
from cooker.recipes.store import RecipeFilter, RecipeStore
from cooker.retrieval.retriever import HybridRetriever

ALLERGEN_NOTICE = "过敏原由关键词规则推导，可能遗漏，请以原料表为准"


class RecipeSummary(BaseModel):
    id: str
    name: str
    category: str
    difficulty: int
    calories: int | None
    time_minutes: int | None
    main_ingredients: list[str]
    flavors: list[str]
    allergens: list[str]

    @classmethod
    def of(cls, r: Recipe) -> RecipeSummary:
        f = r.facts
        return cls(
            id=r.id, name=r.name, category=r.category_name, difficulty=r.difficulty, calories=r.calories,
            time_minutes=f.total_time_minutes if f else None,
            main_ingredients=f.main_ingredients if f else [], flavors=f.flavors if f else [], allergens=r.allergens,
        )


class SearchResult(BaseModel):
    recipe: RecipeSummary
    score: float
    snippets: list[str]


class RecipeDetail(BaseModel):
    summary: RecipeSummary
    description: str
    ingredients: list[str]
    calculation: str
    steps: list[dict]
    extras: str
    images: list[str]
    source: str
    notice: str = ALLERGEN_NOTICE


class TipResult(BaseModel):
    id: str
    title: str
    score: float
    snippets: list[str]


class CookerService:
    def __init__(self, store: RecipeStore, retriever: HybridRetriever | None):
        self.store = store
        self.retriever = retriever

    def _require_retriever(self) -> HybridRetriever:
        if self.retriever is None:
            raise RuntimeError("检索索引未加载，请先运行 cooker-index")
        return self.retriever

    def search_recipes(self, query: str, flt: RecipeFilter | None = None, limit: int = 5) -> list[SearchResult]:
        candidates = {r.id for r in self.store.filter(flt)} if flt else None
        if candidates is not None and not candidates:
            return []
        hits = self._require_retriever().search(query, doc_ids=candidates, doc_type="recipe", top_k=limit)
        return [
            SearchResult(recipe=RecipeSummary.of(self.store.get(h.doc_id)), score=round(h.score, 4),
                         snippets=[e.text[:300] for e in h.evidence])
            for h in hits
        ]

    def search_tips(self, query: str, limit: int = 3) -> list[TipResult]:
        hits = self._require_retriever().search(query, doc_type="tip", top_k=limit)
        return [TipResult(id=h.doc_id, title=h.title, score=round(h.score, 4), snippets=[e.text for e in h.evidence])
                for h in hits]

    def get_recipe(self, name_or_id: str) -> RecipeDetail:
        r = self.store.find(name_or_id)
        return RecipeDetail(
            summary=RecipeSummary.of(r), description=r.description, ingredients=r.ingredients_raw,
            calculation=r.calculation_raw, steps=[g.model_dump() for g in r.steps], extras=r.extras_raw,
            images=r.images, source=r.source_path,
        )

    def browse(self, flt: RecipeFilter | None = None, limit: int = 10, shuffle: bool = False,
               seed: int | None = None) -> list[RecipeSummary]:
        recipes = self.store.filter(flt)
        if shuffle:
            recipes = random.Random(seed).sample(recipes, k=min(limit, len(recipes)))
        return [RecipeSummary.of(r) for r in recipes[:limit]]

    def recommend(self, have: list[str], flt: RecipeFilter | None = None, limit: int = 5) -> list[IngredientMatch]:
        return recommend_by_ingredients(self.store, have, flt, limit)

    def scale(self, name_or_id: str, people: int) -> ScaledRecipe:
        return scale_recipe(self.store.find(name_or_id), people)

    def shopping(self, items: list[tuple[str, int]]) -> ShoppingList:
        if not items:
            raise ValueError("至少提供一道菜")
        return shopping_list([self.scale(name, people) for name, people in items])


@lru_cache
def get_service() -> CookerService:
    """懒加载单例：首次调用时才读数据、建 BM25、连接模型服务。"""
    from cooker.llm.embedding import get_embedder, get_reranker

    store = RecipeStore.load()
    try:
        retriever = HybridRetriever.load(store, get_embedder(), get_reranker())
    except FileNotFoundError:
        retriever = None
    return CookerService(store, retriever)
