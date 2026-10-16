"""规则解析：把 HowToCook 的 Markdown 菜谱切成结构化字段。

依赖上游 .github/manual_lint.js 在 CI 中强制的格式约定：
- 一级标题为「<文件名>的做法」
- 恰好四个二级标题：必备原料和工具 / 计算 / 操作 / 附加内容（按此顺序）
- 标题与第一个二级标题之间有「预估烹饪难度：★…」和「预估卡路里：N 大卡」
- 「操作」一节使用有序列表
约定被破坏时抛 RecipeFormatError，而不是静默产出残缺数据。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path

from cooker.ingest.schema import CATEGORY_NAMES, ParsedRecipe, ParsedTip, StepGroup

REQUIRED_SECTIONS = ["必备原料和工具", "计算", "操作", "附加内容"]

_DIFFICULTY_RE = re.compile(r"^预估烹饪难度：\s*(★{1,5})\s*$")
_CALORIES_RE = re.compile(r"^预估卡路里：\s*(\d+)\s*大卡$")
# 图片可出现在任意位置（含步骤列表项内）；src 允许一层配对括号，兼容「石凉粉(冰粉)成品1.jpg」
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?P<src>(?:[^()\s]|\([^()]*\))+)\)")
_BULLET_RE = re.compile(r"^[-*+]\s+(?P<text>.+)$")
_ORDERED_RE = re.compile(r"^\d+[.、]\s*(?P<text>.+)$")
_NESTED_RE = re.compile(r"^\s+(?:[-*+]|\d+[.、])\s+(?P<text>.+)$")


class RecipeFormatError(ValueError):
    """菜谱不符合上游格式约定。"""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_h2(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """返回（第一个二级标题之前的行，{二级标题: 该节的行}）。"""
    head: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] = head
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            if title in sections:
                raise RecipeFormatError(f"二级标题重复：{title}")
            current = sections.setdefault(title, [])
        else:
            current.append(line)
    return head, sections


def _bullets(lines: list[str]) -> list[str]:
    return [m["text"].strip() for line in lines if (m := _BULLET_RE.match(line.strip()))]


def _parse_steps(lines: list[str]) -> list[StepGroup]:
    """有序列表项为一步；缩进的子列表项附加到上一步末尾；### 标题开启新分组。"""
    groups: list[StepGroup] = []
    current = StepGroup(title=None, steps=[])

    for line in lines:
        if line.startswith("### "):
            if current.steps or current.title:
                groups.append(current)
            current = StepGroup(title=line[4:].strip(), steps=[])
        elif m := _ORDERED_RE.match(line):
            current.steps.append(m["text"].strip())
        elif (m := _NESTED_RE.match(line)) and current.steps:
            current.steps[-1] += f"（{m['text'].strip()}）"
        elif line.strip() and current.steps and not line.startswith("#"):
            # 有序列表项的折行续写（缩进的普通文本）
            if line.startswith((" ", "\t")):
                current.steps[-1] += line.strip()
    if current.steps or current.title:
        groups.append(current)
    return [g for g in groups if g.steps]


def _resolve_image(src: str, md_path: Path, repo_root: Path) -> str:
    src = src.strip()
    if src.startswith(("http://", "https://")):
        return src
    return (md_path.parent / src).resolve().relative_to(repo_root.resolve()).as_posix()


def parse_recipe(md_path: Path, repo_root: Path) -> ParsedRecipe:
    text = md_path.read_text(encoding="utf-8")
    lines = text.replace("\r\n", "\n").split("\n")
    rel = md_path.relative_to(repo_root)
    name = md_path.stem
    # dishes/<category>/... 第二段即分类目录
    category = rel.parts[1]
    if category not in CATEGORY_NAMES:
        raise RecipeFormatError(f"未知分类目录：{category}")

    head, sections = _split_h2(lines)
    if list(sections) != REQUIRED_SECTIONS:
        raise RecipeFormatError(f"二级标题应为 {REQUIRED_SECTIONS}，实际为 {list(sections)}")

    title_lines = [line for line in head if line.startswith("# ")]
    if not title_lines or title_lines[0][2:].strip() != f"{name}的做法":
        raise RecipeFormatError(f"一级标题应为「{name}的做法」")

    difficulty = calories = None
    # 全文按出现顺序收集图片：开头的成品图在前，步骤图在后
    images = list(dict.fromkeys(_resolve_image(m["src"], md_path, repo_root) for m in _IMAGE_RE.finditer(text)))
    desc_lines: list[str] = []
    for line in head:
        s = line.strip()
        if m := _DIFFICULTY_RE.match(s):
            difficulty = len(m[1])
        elif m := _CALORIES_RE.match(s):
            calories = int(m[1])
        elif _IMAGE_RE.match(s):
            continue
        elif s and not s.startswith("#"):
            desc_lines.append(s)
    if difficulty is None:
        raise RecipeFormatError("缺少「预估烹饪难度」")

    return ParsedRecipe(
        id=f"{category}/{name}",
        name=name,
        category=category,
        category_name=CATEGORY_NAMES[category],
        source_path=rel.as_posix(),
        content_hash=content_hash(text),
        description="\n".join(desc_lines),
        difficulty=difficulty,
        calories=calories,
        images=images,
        ingredients_raw=_bullets(sections["必备原料和工具"]),
        calculation_raw="\n".join(sections["计算"]).strip(),
        steps=_parse_steps(sections["操作"]),
        extras_raw="\n".join(sections["附加内容"]).strip(),
        markdown=text,
    )


def parse_tip(md_path: Path, repo_root: Path) -> ParsedTip:
    text = md_path.read_text(encoding="utf-8")
    rel = md_path.relative_to(repo_root)
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), md_path.stem)
    # tips/<sub>/x.md → sub；tips/x.md → general
    category = rel.parts[1] if len(rel.parts) > 2 else "general"
    return ParsedTip(
        id=f"tips/{category}/{md_path.stem}",
        title=title,
        category=category,
        source_path=rel.as_posix(),
        content_hash=content_hash(text),
        markdown=text,
    )


def iter_recipe_files(repo_root: Path) -> Iterator[Path]:
    for path in sorted((repo_root / "dishes").rglob("*.md")):
        if "template" not in path.relative_to(repo_root).parts:
            yield path


def iter_tip_files(repo_root: Path) -> Iterator[Path]:
    yield from sorted((repo_root / "tips").rglob("*.md"))
