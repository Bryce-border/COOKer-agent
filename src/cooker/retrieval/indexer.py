"""建索引：切块 → 向量化（按文本哈希增量缓存）→ 写 data/index/。

产物：
- chunks.json：块列表，顺序与 vectors.npy 的行一一对应
- vectors.npy：(n, dim) float32，已 L2 归一化
- meta.json：模型签名、块数、构建时间
BM25 索引不落盘：2000 个块现建只需几百毫秒，落盘反而多一份需要保持一致的状态。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

from cooker.llm.embedding import Embedder, get_embedder
from cooker.recipes.store import RecipeStore
from cooker.retrieval.chunks import Chunk, recipe_chunks, tip_chunks
from cooker.settings import get_settings

logger = logging.getLogger("cooker.index")


def build_chunks(store: RecipeStore) -> list[Chunk]:
    chunks = [c for r in store.recipes for c in recipe_chunks(r)]
    chunks += [c for t in store.tips for c in tip_chunks(t)]
    ids = [c.id for c in chunks]
    if len(set(ids)) != len(ids):
        raise RuntimeError("chunk id 重复")
    return chunks


class EmbeddingCache:
    """{文本哈希: 向量}，按模型签名分文件存放，换模型自动隔离。"""

    def __init__(self, directory: Path, signature: str):
        safe = signature.replace("@", "_").replace("/", "_")
        self.path = directory / f"emb_{safe}.npz"
        self.data: dict[str, np.ndarray] = {}
        if self.path.exists():
            z = np.load(self.path)
            self.data = dict(zip(z["keys"].tolist(), z["vectors"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = np.array(list(self.data), dtype=object).astype(str)
        vectors = np.stack(list(self.data.values())) if self.data else np.zeros((0, 0), np.float32)
        tmp = self.path.with_name(self.path.stem + ".tmp.npz")
        np.savez(tmp, keys=keys, vectors=vectors)
        tmp.replace(self.path)


def build_index(store: RecipeStore, embedder: Embedder, out_dir: Path, cache_dir: Path) -> dict:
    chunks = build_chunks(store)
    cache = EmbeddingCache(cache_dir, embedder.signature)
    todo = [c for c in chunks if c.text_hash not in cache.data]
    todo = list({c.text_hash: c for c in todo}.values())  # 相同文本只算一次
    logger.info("块 %d 个，需新向量化 %d 个", len(chunks), len(todo))
    if todo:
        t0 = time.perf_counter()
        step = embedder.batch_size * 10
        for i in range(0, len(todo), step):
            part = todo[i : i + step]
            vecs = embedder.embed([c.text for c in part])
            cache.data.update({c.text_hash: v for c, v in zip(part, vecs)})
            cache.save()  # 分段保存，中断后可续跑
            logger.info("向量化进度 %d/%d", min(i + step, len(todo)), len(todo))
        logger.info("向量化耗时 %.1fs", time.perf_counter() - t0)

    vectors = np.stack([cache.data[c.text_hash] for c in chunks]).astype(np.float32)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "vectors.npy", vectors)
    (out_dir / "chunks.json").write_text(
        json.dumps([c.model_dump() for c in chunks], ensure_ascii=False), encoding="utf-8"
    )
    meta = {
        "embedding": embedder.signature,
        "chunks": len(chunks),
        "new_embeddings": len(todo),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main() -> None:
    argparse.ArgumentParser(description="构建检索索引（需先运行 cooker-ingest）").parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    s = get_settings()
    meta = build_index(RecipeStore.load(), get_embedder(), s.data_dir / "index", s.cache_dir / "embeddings")
    logger.info("完成：%s", meta)
    sys.exit(0)


if __name__ == "__main__":
    main()
