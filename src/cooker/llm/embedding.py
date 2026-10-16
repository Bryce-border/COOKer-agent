"""向量化与重排客户端（百炼）。

没有使用 langchain_openai.OpenAIEmbeddings：它默认用 tiktoken 按 OpenAI 规则切分长文本，
对非 OpenAI 模型不适用；而这里只需要「分批调用 + 归一化」，直接用 openai SDK 更透明。
"""

from __future__ import annotations

import logging
from functools import lru_cache

import httpx
import numpy as np
from openai import OpenAI

from cooker.llm.factory import ModelConfigError, endpoint_credentials, load_models_config

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(self) -> None:
        cfg = load_models_config().embedding
        if cfg is None:
            raise ModelConfigError("models.yaml 未配置 embedding")
        base_url, api_key = endpoint_credentials(cfg.endpoint)
        self.model = cfg.model
        self.dimensions = cfg.dimensions
        self.batch_size = cfg.batch_size
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=cfg.timeout, max_retries=3)

    @property
    def signature(self) -> str:
        """模型 + 维度：任何一个变化，旧向量都不能与新向量混用。"""
        return f"{self.model}@{self.dimensions}"

    def embed(self, texts: list[str]) -> np.ndarray:
        """返回 (n, dim) 的 float32 矩阵，每行 L2 归一化，之后点积即余弦相似度。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._client.embeddings.create(model=self.model, input=batch, dimensions=self.dimensions)
            # 按 index 排序，不依赖返回顺序
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        mat = np.asarray(out, dtype=np.float32).reshape(len(texts), self.dimensions)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        return mat / np.clip(norms, 1e-12, None)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


class Reranker:
    def __init__(self) -> None:
        cfg = load_models_config().rerank
        if cfg is None:
            raise ModelConfigError("models.yaml 未配置 rerank")
        base_url, api_key = endpoint_credentials(cfg.endpoint)
        self.model = cfg.model
        self.url = base_url.split("/compatible-mode")[0].rstrip("/") + cfg.path
        self._client = httpx.Client(timeout=cfg.timeout, headers={"Authorization": f"Bearer {api_key}"})

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
        """返回 [(原文档下标, 相关性分数)]，按分数降序。网络或接口错误直接抛出，由调用方决定降级。"""
        if not documents:
            return []
        body = {"model": self.model, "query": query, "documents": documents, "top_n": top_n or len(documents)}
        resp = self._client.post(self.url, json=body)
        resp.raise_for_status()
        results = resp.json()["results"]
        return [(r["index"], float(r["relevance_score"])) for r in results]


@lru_cache
def get_embedder() -> Embedder:
    return Embedder()


@lru_cache
def get_reranker() -> Reranker:
    return Reranker()
