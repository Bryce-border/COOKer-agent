import hashlib

import numpy as np
import pytest

from cooker.ingest.schema import Ingredient, Recipe, RecipeFacts, StepGroup
from cooker.settings import get_settings


@pytest.fixture(scope="session")
def repo_root():
    root = get_settings().howtocook_dir
    if not (root / "dishes").is_dir():
        pytest.skip("HowToCook 数据目录不存在")
    return root


@pytest.fixture(scope="session")
def real_store():
    from cooker.recipes.store import RecipeStore

    try:
        return RecipeStore.load()
    except FileNotFoundError:
        pytest.skip("data/recipes.json 不存在，先运行 cooker-ingest")


# ---------- 构造测试数据 ----------


def make_recipe(name, category="vegetable_dish", ingredients=(), main=(), serving=2.0, difficulty=2,
                vegetarian=True, allergens=(), raw=None, flavors=()):
    ings = [Ingredient(**i) if isinstance(i, dict) else i for i in ingredients] or [
        Ingredient(name=name, amount=1, unit="个", scaling="per_serving")
    ]
    return Recipe(
        id=f"{category}/{name}", name=name, category=category, category_name="素菜",
        source_path=f"dishes/{category}/{name}.md", content_hash="x", description=f"{name}的介绍",
        difficulty=difficulty, calories=300, images=[], ingredients_raw=list(raw or [i.name for i in ings]),
        calculation_raw="", extras_raw="", markdown=f"# {name}的做法", steps=[StepGroup(title=None, steps=["做"])],
        facts=RecipeFacts(
            serving_size_people=serving, ingredients=ings, tools=[], main_ingredients=list(main or [ings[0].name]),
            flavors=list(flavors), methods=["炒"], is_vegetarian=vegetarian, total_time_minutes=20,
        ),
        allergens=list(allergens),
    )


class FakeEmbedder:
    """字符哈希词袋向量：确定性、无网络，语义能力弱但足以测试流程。"""

    signature = "fake@64"
    batch_size = 20

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise ConnectionError("embedding down")
        m = np.zeros((len(texts), 64), np.float32)
        for i, t in enumerate(texts):
            for ch in t:
                m[i, int(hashlib.md5(ch.encode()).hexdigest(), 16) % 64] += 1
        return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-12, None)

    def embed_query(self, text):
        return self.embed([text])[0]


class FakeReranker:
    """把菜名出现在 query 里的文档排到最前，便于断言重排生效。"""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def rerank(self, query, documents, top_n=None):
        self.calls += 1
        if self.fail:
            raise ConnectionError("rerank down")
        scored = [(i, 1.0 if d.split("】")[0].lstrip("【") in query else 0.1) for i, d in enumerate(documents)]
        return sorted(scored, key=lambda x: -x[1])


def build_retriever(store, embedder=None, reranker=None):
    from cooker.retrieval.indexer import build_chunks
    from cooker.retrieval.retriever import HybridRetriever

    chunks = build_chunks(store)
    vectors = FakeEmbedder().embed([c.text for c in chunks])
    return HybridRetriever(store, chunks, vectors, embedder or FakeEmbedder(), reranker)
