"""检索消融评测：BM25 / 向量 / 混合 / 混合+重排，指标 Hit@1、Hit@5、MRR@5。

用法：PYTHONUTF8=1 .venv/Scripts/python eval/run_retrieval_eval.py [--verbose]
"""
import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

import yaml

from cooker.service import get_service

CONFIGS = [("bm25", "bm25", False), ("vector", "vector", False), ("hybrid", "hybrid", False), ("hybrid+rerank", "hybrid", True)]


def evaluate(retriever, cases, doc_type, mode, rerank):
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        hits = retriever.search(c["q"], doc_type=doc_type, top_k=5, mode=mode, use_rerank=rerank)
        ids = [h.doc_id for h in hits]
        rank = next((i + 1 for i, d in enumerate(ids) if d in c["expect"]), None)
        rows.append({**c, "rank": rank, "top": ids[:3], "ms": (time.perf_counter() - t0) * 1000})
    return rows


def summarize(rows):
    n = len(rows)
    return {
        "hit@1": sum(r["rank"] == 1 for r in rows) / n,
        "hit@5": sum(r["rank"] is not None for r in rows) / n,
        "mrr@5": sum(1 / r["rank"] for r in rows if r["rank"]) / n,
        "avg_ms": sum(r["ms"] for r in rows) / n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    cases = yaml.safe_load(Path(__file__).with_name("retrieval_cases.yaml").read_text(encoding="utf-8"))
    retriever = get_service().retriever
    report = {}
    for name, mode, rerank in CONFIGS:
        rec = evaluate(retriever, cases["recipe"], "recipe", mode, rerank)
        tip = evaluate(retriever, cases["tip"], "tip", mode, rerank)
        by_type = defaultdict(list)
        for r in rec:
            by_type[r["type"]].append(r)
        report[name] = {"recipe": summarize(rec), "tip": summarize(tip),
                        **{f"recipe/{t}": summarize(rs) for t, rs in by_type.items()}}
        if args.verbose:
            for r in rec + tip:
                if r["rank"] != 1:
                    print(f"  [{name}] rank={r['rank']} {r['q']} → {r['top']}")

    cols = ["recipe", "recipe/name", "recipe/synonym", "recipe/describe", "recipe/step", "tip"]
    print(f"{'config':15}" + "".join(f"{c:>22}" for c in cols) + f"{'avg_ms':>9}")
    for name, m in report.items():
        cells = "".join(f"{m[c]['hit@1']:>9.2f}/{m[c]['hit@5']:.2f}/{m[c]['mrr@5']:.2f}" for c in cols)
        print(f"{name:15}{cells}{m['recipe']['avg_ms']:>9.0f}")
    print("单元格：Hit@1 / Hit@5 / MRR@5；样本数：recipe", len(cases["recipe"]), "tip", len(cases["tip"]))
    if retriever.stats:
        print(f"⚠ 评测期间发生降级 {dict(retriever.stats)}：结果不能代表完整检索链路，已作废")
        raise SystemExit(1)
    out = Path("data/eval_retrieval.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
