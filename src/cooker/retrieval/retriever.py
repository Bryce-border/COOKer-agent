"""混合检索：BM25 + 向量 → RRF 融合 → 按文档聚合 → 重排。

流程：
    query ─┬─ BM25(jieba)  ─┐
           └─ 向量(余弦)   ─┴─ RRF → 块分数按 doc_id 聚合（取最高）→ Reranker → top_k
结构化过滤在融合之前做：先确定候选文档集合，再只在其中的块上打分。
"""

from __future__ import annotations

import json
import logging
import re
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from cooker.recipes.store import RecipeStore
from cooker.retrieval.chunks import Chunk, recipe_overview_text
from cooker.settings import get_settings

with warnings.catch_warnings():  # jieba 源码含无效转义序列，首次编译时会刷 SyntaxWarning
    warnings.simplefilter("ignore", SyntaxWarning)
    import jieba

logger = logging.getLogger(__name__)

RRF_K = 60  # RRF 常数：论文与业界常用值，削弱单一路排名靠前的影响

_TOKEN_RE = re.compile(r"[\w一-鿿]+")
_STOPWORDS = frozenset("的 了 和 与 及 或 把 被 在 是 用 要 就 都 也 再 将 后 前 时 中 上 下 个 一 怎么 如何 什么 吗 呢 做 做法 可以".split())
_jieba_ready = False


def _init_jieba(vocab: list[str]) -> None:
    """jieba 默认把词典缓存写到系统临时目录；改到项目 .cache，并加入菜名 / 食材名作为自定义词。"""
    global _jieba_ready
    if _jieba_ready:
        return
    cache = get_settings().data_dir.parent / ".cache" / "jieba"
    cache.mkdir(parents=True, exist_ok=True)
    jieba.dt.tmp_dir = str(cache)
    jieba.setLogLevel(logging.WARNING)
    for word in vocab:
        if len(word) >= 2:
            jieba.add_word(word, freq=2000)
    _jieba_ready = True


def tokenize(text: str) -> list[str]:
    # cut_for_search 会对长词再切分：「可乐鸡翅」→ 可乐 / 鸡翅 / 可乐鸡翅，兼顾整词与子词命中
    return [
        t.lower()
        for t in jieba.cut_for_search(text)
        if _TOKEN_RE.fullmatch(t) and t not in _STOPWORDS
    ]


class CircuitBreaker:
    """熔断器：连续失败 threshold 次后「断开」cooldown 秒，期间直接走降级路径，不再请求。

    没有熔断时，外部服务故障会让每次查询都白等超时 + 打一条堆栈；
    断开期过后放行一次试探请求（半开），成功即恢复。
    """

    def __init__(self, threshold: int = 2, cooldown: float = 60.0, clock=time.monotonic):
        # clock 可注入：测试用假时钟推进时间，不依赖 sleep（Windows 上 monotonic 精度仅约 15.6ms）
        self.threshold, self.cooldown, self.clock = threshold, cooldown, clock
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> bool:
        return self.clock() >= self.open_until

    def success(self) -> None:
        self.failures = 0

    def failure(self) -> bool:
        """记录失败；返回 True 表示本次触发了断开。"""
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = self.clock() + self.cooldown
            self.failures = 0
            return True
        return False


class Evidence(BaseModel):
    chunk_id: str
    title: str
    text: str


class Hit(BaseModel):
    doc_id: str
    doc_type: Literal["recipe", "tip"]
    title: str
    score: float
    rrf_score: float
    reranked: bool
    evidence: list[Evidence]


class HybridRetriever:
    def __init__(self, store: RecipeStore, chunks: list[Chunk], vectors: np.ndarray, embedder, reranker=None):
        if len(chunks) != len(vectors):
            raise ValueError("chunks 与 vectors 行数不一致，请重新运行 cooker-index")
        self.store = store
        self.chunks = chunks
        self.vectors = vectors
        self.embedder = embedder
        self.reranker = reranker
        self.breakers = {"vector": CircuitBreaker(), "rerank": CircuitBreaker()}
        self.stats: Counter[str] = Counter()  # 降级次数，供评测与监控判断结果是否可信
        vocab = [r.name for r in store.recipes]
        vocab += [i.name for r in store.recipes if r.facts for i in r.facts.ingredients]
        _init_jieba(vocab)
        self.bm25 = BM25Okapi([tokenize(c.text) for c in chunks])
        self._doc_rows: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            self._doc_rows[c.doc_id].append(i)

    @classmethod
    def load(cls, store: RecipeStore, embedder, reranker=None, index_dir: Path | None = None) -> HybridRetriever:
        index_dir = index_dir or get_settings().data_dir / "index"
        meta = json.loads((index_dir / "meta.json").read_text(encoding="utf-8"))
        if meta["embedding"] != embedder.signature:
            raise RuntimeError(f"索引由 {meta['embedding']} 构建，当前配置为 {embedder.signature}，请重新运行 cooker-index")
        chunks = [Chunk.model_validate(x) for x in json.loads((index_dir / "chunks.json").read_text(encoding="utf-8"))]
        return cls(store, chunks, np.load(index_dir / "vectors.npy"), embedder, reranker)

    # ---- 各路召回：返回按分数降序的行号 ----

    def _bm25_rank(self, query: str, mask: np.ndarray, n: int) -> list[int]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = np.where(mask, self.bm25.get_scores(tokens), -np.inf)
        order = np.argsort(-scores)[:n]
        return [int(i) for i in order if scores[i] > 0]

    def _vector_rank(self, query: str, mask: np.ndarray, n: int) -> list[int]:
        q = self.embedder.embed_query(query)
        scores = np.where(mask, self.vectors @ q, -np.inf)  # 已归一化：点积 = 余弦相似度
        order = np.argsort(-scores)[:n]
        return [int(i) for i in order if np.isfinite(scores[i])]

    @staticmethod
    def _rrf(*rankings: list[int]) -> dict[int, float]:
        fused: dict[int, float] = defaultdict(float)
        for ranking in rankings:
            for rank, row in enumerate(ranking):
                fused[row] += 1.0 / (RRF_K + rank + 1)
        return fused

    def search(
        self,
        query: str,
        doc_ids: set[str] | None = None,
        doc_type: Literal["recipe", "tip"] | None = None,
        top_k: int = 5,
        recall_n: int = 50,
        rerank_n: int = 20,
        mode: Literal["hybrid", "bm25", "vector"] = "hybrid",
        use_rerank: bool = True,
    ) -> list[Hit]:
        """doc_ids：结构化过滤后的候选文档（None 表示不限）。mode / use_rerank 用于消融评测。"""
        query = query.strip()
        if not query:
            return []
        mask = np.array(
            [(doc_type is None or c.doc_type == doc_type) and (doc_ids is None or c.doc_id in doc_ids) for c in self.chunks]
        )
        if not mask.any():
            return []

        vec_rank: list[int] = []
        if mode in ("hybrid", "vector"):
            vec_rank = self._guarded("vector", lambda: self._vector_rank(query, mask, recall_n), [])
        bm25_rank = self._bm25_rank(query, mask, recall_n) if mode in ("hybrid", "bm25") else []
        fused = self._rrf(bm25_rank, vec_rank)

        # 父子聚合：文档分 = 其最佳块的分；保留最好的 2 个块作为证据
        by_doc: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for row, score in fused.items():
            by_doc[self.chunks[row].doc_id].append((score, row))
        docs = sorted(by_doc.items(), key=lambda kv: -max(s for s, _ in kv[1]))[:rerank_n]
        hits = []
        for doc_id, scored in docs:
            scored.sort(reverse=True)
            first = self.chunks[scored[0][1]]
            hits.append(
                Hit(
                    doc_id=doc_id,
                    doc_type=first.doc_type,
                    title=self._doc_title(doc_id, first),
                    score=scored[0][0],
                    rrf_score=scored[0][0],
                    reranked=False,
                    evidence=[Evidence(chunk_id=self.chunks[r].id, title=self.chunks[r].title, text=self.chunks[r].text)
                              for _, r in scored[:2]],
                )
            )
        return (self._rerank(query, hits) if use_rerank else hits)[:top_k]

    def _guarded(self, name: str, fn, fallback):
        """带熔断的外部调用：失败返回 fallback 并计数，日志只在首次失败和断开时输出。"""
        breaker = self.breakers[name]
        if not breaker.allow():
            self.stats[f"{name}_skipped"] += 1
            return fallback
        try:
            result = fn()
        except Exception as e:
            self.stats[f"{name}_failed"] += 1
            opened = breaker.failure()
            msg = f"{name} 调用失败（{type(e).__name__}: {str(e)[:200]}），已降级"
            if opened:
                logger.warning("%s；熔断 %ds", msg, breaker.cooldown)
            else:
                logger.warning(msg)
            return fallback
        breaker.success()
        return result

    def _doc_title(self, doc_id: str, chunk: Chunk) -> str:
        if r := self.store.get(doc_id):
            return r.name
        tip = self.store.get_tip(doc_id)
        return tip.title if tip else chunk.title

    def _rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        if not self.reranker or len(hits) < 2:
            return hits
        docs = []
        for h in hits:
            r = self.store.get(h.doc_id)
            head = recipe_overview_text(r) if r else h.title
            body = "\n".join(e.text for e in h.evidence if not e.chunk_id.endswith("#overview"))
            docs.append(f"{head}\n{body}"[:1500])
        # 重排是锦上添花：失败时退回 RRF 排序，检索不中断
        ranked = self._guarded("rerank", lambda: self.reranker.rerank(query, docs), None)
        if ranked is None:
            return hits
        out = []
        for idx, score in ranked:
            hits[idx].score, hits[idx].reranked = score, True
            out.append(hits[idx])
        return out
