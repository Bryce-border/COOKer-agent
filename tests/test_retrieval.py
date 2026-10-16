from conftest import FakeEmbedder, FakeReranker, build_retriever
from cooker.retrieval.chunks import recipe_chunks
from cooker.retrieval.retriever import CircuitBreaker, HybridRetriever, tokenize


def test_chunks_have_header_and_no_boilerplate(real_store):
    chunks = recipe_chunks(real_store.find("乡村啤酒鸭"))
    assert all(c.text.startswith("【乡村啤酒鸭】") for c in chunks)
    assert not any("请提出 Issue" in c.text for c in chunks)
    assert {c.kind for c in chunks} >= {"overview", "steps"}


def test_tokenize_keeps_dish_names(real_store):
    build_retriever(real_store)  # 初始化自定义词典
    tokens = tokenize("可乐鸡翅怎么做")
    assert "可乐鸡翅" in tokens and "怎么" not in tokens


def test_rrf_rewards_agreement():
    fused = HybridRetriever._rrf([1, 2, 3], [3, 1, 4])
    assert max(fused, key=fused.get) == 1  # 两路都靠前
    assert fused[3] > fused[2] and 4 in fused


def test_search_respects_filter_and_type(real_store):
    rt = build_retriever(real_store)
    ids = {"meat_dish/可乐鸡翅", "meat_dish/红烧鸡翅"}
    hits = rt.search("鸡翅", doc_ids=ids, doc_type="recipe", top_k=5)
    assert hits and {h.doc_id for h in hits} <= ids
    tips = rt.search("焯水", doc_type="tip", top_k=3)
    assert tips and all(h.doc_type == "tip" for h in tips)


def test_rerank_reorders(real_store):
    rr = FakeReranker()
    hits = build_retriever(real_store, reranker=rr).search("可乐鸡翅 甜口", doc_type="recipe", top_k=3)
    assert rr.calls == 1 and hits[0].doc_id == "meat_dish/可乐鸡翅" and hits[0].reranked


def test_degrades_and_circuit_opens(real_store):
    emb, rr = FakeEmbedder(fail=True), FakeReranker(fail=True)
    rt = build_retriever(real_store, embedder=emb, reranker=rr)
    for _ in range(4):
        hits = rt.search("可乐鸡翅", doc_type="recipe")
        assert hits and hits[0].doc_id == "meat_dish/可乐鸡翅"  # BM25 仍能找到
        assert not hits[0].reranked
    assert emb.calls == 2 and rr.calls == 2  # 两次失败后熔断，不再请求
    assert rt.stats["vector_failed"] == 2 and rt.stats["vector_skipped"] == 2


def test_circuit_breaker_half_open():
    now = [0.0]
    b = CircuitBreaker(threshold=2, cooldown=60, clock=lambda: now[0])
    assert not b.failure() and b.allow()  # 第 1 次失败：未达阈值
    assert b.failure() and not b.allow()  # 第 2 次：断开
    now[0] = 59.9
    assert not b.allow()
    now[0] = 60.0
    assert b.allow()                      # 冷却结束：放行试探请求
