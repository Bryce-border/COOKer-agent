"""LLM 结构化抽取：从菜谱原文抽取 RecipeFacts（用量、口味、过敏原等），带内容哈希缓存。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import ValidationError

from cooker.ingest.schema import ParsedRecipe, RecipeFacts
from cooker.llm import build_runnable

logger = logging.getLogger(__name__)

# 修改 Prompt 或 RecipeFacts 结构时递增，使旧缓存自动失效
PROMPT_VERSION = "facts-v2"

SYSTEM_PROMPT = """你是严谨的菜谱数据标注员。根据给定的中文菜谱 Markdown，抽取结构化信息。
规则：
1. 只依据原文，不要编造原文没有的食材或用量；无法确定的字段填 null 或空列表。
2. ingredients 以「计算」一节为准，逐条列出带用量的原料；原文是公式（如 份数 * 0.7 个、面粉 / 4）时，
   换算成每份或每人的具体数值，并在 note 中保留原公式。「必备原料和工具」中的食材若「计算」未给用量，
   也要列出（scaling=to_taste），不要遗漏主料；厨具不算食材。
   原文用小标题或冒号行分组（如「### 香料」「葱油酱汁基础量：」）时，把分组名填入 group。
3. scaling 判断：写「每人」「/人」用 per_person；写「每份」或说明一份够几人用 per_serving；
   与份数无关的固定量、一次性制作的基础量（如酱汁基础量）用 fixed；适量、少许用 to_taste。
4. 单位统一：克→g，毫升→ml，千克/kg→换算为 g；个、根、瓣、片等保留原文。
5. 食材名规范化：去掉用量、形容词和括号说明，例如「鸡蛋 400g（约 8 颗）」→ name=鸡蛋, amount=400, unit=g, note=约 8 颗。
6. 标签只能从字段说明给出的候选值中选择。"""


def cache_key(recipe: ParsedRecipe) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}:{recipe.content_hash}".encode()).hexdigest()[:32]


@dataclass
class ExtractResult:
    recipe_id: str
    facts: RecipeFacts | None
    model: str | None = None
    cached: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class FactsCache:
    """一个菜谱一个 JSON 文件：读写简单、天然支持增量、单个文件损坏不影响其它。"""

    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, recipe: ParsedRecipe) -> ExtractResult | None:
        path = self._path(cache_key(recipe))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractResult(recipe.id, RecipeFacts.model_validate(data["facts"]), data.get("model"), cached=True)
        except (json.JSONDecodeError, KeyError, ValidationError):
            logger.warning("缓存损坏，将重新抽取：%s", path.name)
            return None

    def put(self, recipe: ParsedRecipe, result: ExtractResult) -> None:
        payload = {
            "recipe_id": recipe.id,
            "prompt_version": PROMPT_VERSION,
            "content_hash": recipe.content_hash,
            "model": result.model,
            "facts": result.facts.model_dump(mode="json") if result.facts else None,
        }
        tmp = self._path(cache_key(recipe)).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(cache_key(recipe)))  # 原子替换，避免中断时留下半个文件


def _structured(model: BaseChatModel) -> Runnable:
    """把模型包装为「输入消息 → (RecipeFacts, 原始响应)」，解析失败时抛异常以触发重试。"""

    def unwrap(out: dict[str, Any]) -> tuple[RecipeFacts, Any]:
        if out.get("parsing_error") or out.get("parsed") is None:
            raise OutputParserException(f"结构化输出解析失败：{out.get('parsing_error')}")
        return out["parsed"], out["raw"]

    chain = model.with_structured_output(RecipeFacts, method="function_calling", include_raw=True) | RunnableLambda(unwrap)
    # 先在同一厂商内重试格式错误，仍失败再交给外层 fallback
    return chain.with_retry(retry_if_exception_type=(OutputParserException, ValidationError), stop_after_attempt=2)


def build_extractor(provider: str | None = None) -> Runnable:
    return build_runnable("extract", transform=_structured, provider=provider)


def _messages(recipe: ParsedRecipe) -> list:
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(f"菜谱 ID：{recipe.id}\n\n{recipe.markdown}")]


async def extract_all(
    recipes: list[ParsedRecipe],
    cache: FactsCache,
    extractor: Runnable,
    concurrency: int = 8,
    on_done=None,
) -> list[ExtractResult]:
    sem = asyncio.Semaphore(concurrency)

    async def one(recipe: ParsedRecipe) -> ExtractResult:
        if hit := cache.get(recipe):
            return hit
        async with sem:
            try:
                facts, raw = await extractor.ainvoke(_messages(recipe))
                result = ExtractResult(
                    recipe.id,
                    facts,
                    model=raw.response_metadata.get("model_name"),
                    usage=dict(raw.usage_metadata or {}),
                )
                cache.put(recipe, result)
            except Exception as e:  # 单条失败不影响整体，记录后继续
                logger.exception("抽取失败：%s", recipe.id)
                result = ExtractResult(recipe.id, None, error=f"{type(e).__name__}: {e}"[:500])
        if on_done:
            on_done(result)
        return result

    return await asyncio.gather(*(one(r) for r in recipes))
