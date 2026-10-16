"""数据入库流水线：解析全部菜谱 → LLM 抽取 facts（增量）→ 输出 data/recipes.json、data/tips.json。

用法：
    cooker-ingest                 # 全量（已缓存的不会重复调用 LLM）
    cooker-ingest --no-llm        # 只做规则解析
    cooker-ingest --limit 5       # 只抽取前 5 道，用于调试 Prompt
    cooker-ingest --only meat_dish/可乐鸡翅 --provider deepseek
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from cooker.ingest.allergens import derive_allergens
from cooker.ingest.extractor import FactsCache, build_extractor, extract_all
from cooker.ingest.parser import RecipeFormatError, iter_recipe_files, iter_tip_files, parse_recipe, parse_tip
from cooker.ingest.schema import Recipe
from cooker.settings import get_settings

logger = logging.getLogger("cooker.ingest")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def run(no_llm: bool, limit: int | None, only: list[str] | None, provider: str | None, concurrency: int) -> int:
    settings = get_settings()
    root = settings.howtocook_dir
    if not (root / "dishes").is_dir():
        logger.error("找不到菜谱目录：%s（可用 COOKER_HOWTOCOOK_DIR 指定）", root / "dishes")
        return 2

    parsed, parse_errors = [], []
    for path in iter_recipe_files(root):
        try:
            parsed.append(parse_recipe(path, root))
        except RecipeFormatError as e:
            parse_errors.append({"path": path.relative_to(root).as_posix(), "error": str(e)})
    tips = [parse_tip(p, root) for p in iter_tip_files(root)]
    logger.info("规则解析：菜谱 %d 道，失败 %d，技巧 %d 篇", len(parsed), len(parse_errors), len(tips))

    targets = [r for r in parsed if not only or r.id in only]
    if limit:
        targets = targets[:limit]

    results = {}
    if not no_llm and targets:
        cache = FactsCache(settings.cache_dir / "facts")
        done = 0

        def progress(res):
            nonlocal done
            done += 1
            if done % 20 == 0 or done == len(targets):
                logger.info("抽取进度 %d/%d", done, len(targets))

        t0 = time.perf_counter()
        out = asyncio.run(extract_all(targets, cache, build_extractor(provider), concurrency, progress))
        results = {r.recipe_id: r for r in out}
        logger.info("抽取耗时 %.1fs", time.perf_counter() - t0)

    # 未参与本次抽取的菜谱，也尝试从缓存补齐 facts，保证输出文件完整
    cache = FactsCache(settings.cache_dir / "facts")
    recipes = []
    for r in parsed:
        res = results.get(r.id) or (None if no_llm else cache.get(r))
        facts = res.facts if res else None
        names = [i.name for i in facts.ingredients] if facts else []
        recipes.append(
            Recipe(
                **r.model_dump(),
                facts=facts,
                facts_model=res.model if res else None,
                allergens=derive_allergens([*names, *r.ingredients_raw]),
            )
        )

    _write_json(settings.data_dir / "recipes.json", [r.model_dump(mode="json") for r in recipes])
    _write_json(settings.data_dir / "tips.json", [t.model_dump(mode="json") for t in tips])

    failed = [{"id": r.recipe_id, "error": r.error} for r in results.values() if r.error]
    usage = Counter()
    for r in results.values():
        usage.update({k: v for k, v in r.usage.items() if isinstance(v, int)})
    report = {
        "recipes": len(recipes),
        "with_facts": sum(1 for r in recipes if r.facts),
        "parse_errors": parse_errors,
        "extract_attempted": len(results),
        "extract_cached": sum(1 for r in results.values() if r.cached),
        "extract_failed": failed,
        "models": Counter(r.model for r in results.values() if r.model and not r.cached),
        "token_usage": dict(usage),
    }
    _write_json(settings.data_dir / "ingest_report.json", report)
    logger.info(
        "完成：%d 道菜，其中 %d 道有 facts；本次新抽取 %d，命中缓存 %d，失败 %d；tokens=%s",
        report["recipes"], report["with_facts"], len(results) - report["extract_cached"],
        report["extract_cached"], len(failed), dict(usage),
    )
    return 1 if (parse_errors or failed) else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="HowToCook 菜谱入库")
    parser.add_argument("--no-llm", action="store_true", help="只做规则解析，不调用 LLM")
    parser.add_argument("--limit", type=int, help="只抽取前 N 道")
    parser.add_argument("--only", nargs="*", help="只抽取指定 ID")
    parser.add_argument("--provider", help="覆盖默认厂商，如 qwen / deepseek")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    sys.exit(run(args.no_llm, args.limit, args.only, args.provider, args.concurrency))


if __name__ == "__main__":
    main()
