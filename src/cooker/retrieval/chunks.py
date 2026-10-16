"""切块：菜谱按「概览 / 操作分组 / 附加内容」切，技巧文章按标题切。

每个块的文本都带上所属菜名作为前缀（contextual chunk header）。否则「加入 1000 ml 啤酒」
这种步骤块单独看不出是哪道菜，向量和关键词检索都会丢失上下文。
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel

from cooker.ingest.schema import ParsedTip, Recipe

MAX_CHARS = 700  # 单块软上限；超出按条目 / 段落再切

ChunkKind = Literal["overview", "steps", "extras", "tip"]

# 上游每篇菜谱末尾都有的模板话术，对检索是纯噪声
_BOILERPLATE_RE = re.compile(r"如果您遵循本指南的制作流程而发现有问题或可以改进的流程")


class Chunk(BaseModel):
    id: str
    doc_id: str
    doc_type: Literal["recipe", "tip"]
    kind: ChunkKind
    title: str
    text: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _windows(header: str, items: list[str], sep: str = "\n") -> list[str]:
    """把条目按顺序装箱，每箱不超过 MAX_CHARS；每箱都带 header。"""
    boxes, cur = [], []
    for item in items:
        if cur and len(header) + sum(len(x) + 1 for x in cur) + len(item) > MAX_CHARS:
            boxes.append(cur)
            cur = []
        cur.append(item)
    if cur:
        boxes.append(cur)
    return [header + sep.join(b) for b in boxes]


def recipe_overview_text(r: Recipe) -> str:
    """概览块：也用作重排时代表整道菜的文本。"""
    lines = [f"【{r.name}】{r.category_name}，难度 {'★' * r.difficulty}，约 {r.calories} 大卡", r.description]
    if r.facts:
        f = r.facts
        tags = [*f.flavors, *f.methods, "素食" if f.is_vegetarian else ""]
        lines.append(f"主料：{'、'.join(f.main_ingredients)}；特点：{'、'.join(t for t in tags if t)}")
    lines.append(f"原料和工具：{'、'.join(r.ingredients_raw)}")
    return "\n".join(x for x in lines if x)


def recipe_chunks(r: Recipe) -> list[Chunk]:
    out = [Chunk(id=f"{r.id}#overview", doc_id=r.id, doc_type="recipe", kind="overview", title=r.name,
                 text=recipe_overview_text(r))]
    calc = f"【{r.name}】用量计算\n{r.calculation_raw}"
    out.append(Chunk(id=f"{r.id}#calc", doc_id=r.id, doc_type="recipe", kind="overview", title=f"{r.name} · 用量",
                     text=calc[: MAX_CHARS * 2]))
    for gi, group in enumerate(r.steps):
        label = group.title or "操作"
        header = f"【{r.name}】{label}\n"
        steps = [f"{i + 1}. {s}" for i, s in enumerate(group.steps)]
        for wi, text in enumerate(_windows(header, steps)):
            out.append(Chunk(id=f"{r.id}#steps-{gi}-{wi}", doc_id=r.id, doc_type="recipe", kind="steps",
                             title=f"{r.name} · {label}", text=text))
    paras = [p for p in r.extras_raw.split("\n") if p.strip() and not _BOILERPLATE_RE.search(p)]
    if paras:
        for wi, text in enumerate(_windows(f"【{r.name}】附加内容\n", paras)):
            out.append(Chunk(id=f"{r.id}#extras-{wi}", doc_id=r.id, doc_type="recipe", kind="extras",
                             title=f"{r.name} · 附加内容", text=text))
    return out


_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$")


def tip_chunks(t: ParsedTip) -> list[Chunk]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in t.markdown.splitlines():
        if line.startswith("# "):
            continue
        if m := _HEADING_RE.match(line):
            sections.append((m[2].strip(), []))
        elif line.strip() and not line.lstrip().startswith("!["):
            sections[-1][1].append(line.strip())
    out = []
    for si, (heading, lines) in enumerate(sections):
        if not lines:
            continue
        title = f"{t.title} · {heading}" if heading else t.title
        for wi, text in enumerate(_windows(f"【{title}】\n", lines)):
            out.append(Chunk(id=f"{t.id}#{si}-{wi}", doc_id=t.id, doc_type="tip", kind="tip", title=title, text=text))
    return out
