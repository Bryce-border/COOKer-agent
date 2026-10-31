"""Agent Skill 按需加载（渐进式披露，与 Claude Skills 同一思路）。

三层上下文，用到哪层才付哪层的 token：
1. 索引：skills/*/SKILL.md 的 frontmatter（name + description）常驻系统提示词，每个技能一两行；
2. 正文：模型判断请求命中某技能时调用 load_skill → 状态 skills 记下它 → 之后每次调用模型时
   系统提示词附上 SKILL.md 正文 + 宿主适配说明，并绑定该技能的工具；
3. 参考模块：正文里提到的 references/*.md，模型需要时用 read_skill_file 读取。

为什么正文放系统提示词而不是工具返回值：历史消息会按 token 预算裁剪（graph.py），
长正文若作为一条 ToolMessage 很快会被裁掉，规则就「失忆」了；放在系统提示词里整个会话都稳定生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import Field

from cooker.settings import PROJECT_ROOT

SKILLS_DIR = PROJECT_ROOT / "skills"
MAX_FILE_CHARS = 20_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    root: Path
    body: str  # SKILL.md 去掉 frontmatter 后的正文
    adapter: str = ""  # 宿主适配说明，附在正文之后
    tools: tuple[BaseTool, ...] = ()

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in (self.root / "references").glob("*.md"))


def parse_skill_md(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md 缺少 frontmatter")
    _, meta, body = text.split("---", 2)
    return yaml.safe_load(meta), body.strip()


def _extensions() -> dict[str, tuple[str, tuple[BaseTool, ...]]]:
    """宿主为特定技能提供的适配说明与工具。延迟导入：不用麦当劳技能的场景不加载 MCP 依赖。"""
    from cooker.mcd.tools import MCD_TOOLS, SKILL_NAME

    adapter = (Path(__file__).parent / "mcd" / "host_adapter.md").read_text(encoding="utf-8")
    return {SKILL_NAME: (adapter, tuple(MCD_TOOLS))}


@lru_cache
def available() -> dict[str, Skill]:
    ext = _extensions()
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        meta, body = parse_skill_md(path.read_text(encoding="utf-8"))
        name = meta["name"]
        adapter, tools = ext.get(name, ("", ()))
        skills[name] = Skill(name, " ".join(str(meta["description"]).split()), path.parent, body, adapter, tools)
    return skills


def index_prompt() -> str:
    lines = [f"- `{s.name}`：{s.description}" for s in available().values()]
    return "\n".join(lines) or "（无）"


def active_prompt(names: list[str]) -> str:
    parts = []
    for name in names:
        if skill := available().get(name):
            parts.append(f"# 已加载技能：{name}\n\n{skill.body}\n\n{skill.adapter}".rstrip())
    return "\n\n---\n\n".join(parts)


def active_tools(names: list[str]) -> list[BaseTool]:
    return [t for n in names if (s := available().get(n)) for t in s.tools]


def all_skill_tools() -> list[BaseTool]:
    return [t for s in available().values() for t in s.tools]


@tool
def load_skill(name: Annotated[str, Field(description="技能名，取自系统提示词中的「可用技能」列表")],
               runtime: ToolRuntime) -> Command:
    """加载一个技能：之后的回合会获得它的完整说明与专用工具。用户的请求命中某技能的描述时先调用本工具；
    同一会话中已加载过的技能不需要重复加载。"""
    skill = available().get(name)
    if skill is None:
        content = f"没有名为「{name}」的技能。可用：{list(available())}"
        return Command(update={"messages": [ToolMessage(content, tool_call_id=runtime.tool_call_id)]})
    content = (f"已加载技能「{name}」：完整说明已加入系统提示词，按其执行。"
               f"新增 {len(skill.tools)} 个专用工具。可用 read_skill_file 读取的参考模块：{skill.files()}")
    if skill.tools:
        content += f"\n账户连接：{_connection_status()}"
    return Command(update={"skills": [name],
                           "messages": [ToolMessage(content, tool_call_id=runtime.tool_call_id)]})


def _connection_status() -> str:
    from cooker.mcd.client import McdError, load_settings

    try:
        load_settings()
        return "已配置密钥（是否有效以首次查询为准）"
    except McdError as e:
        return f"不可用（{e}）。只能做不需要账户的内容（如人格测试、运势），并如实告知用户"


@tool
def read_skill_file(
    name: Annotated[str, Field(description="技能名")],
    path: Annotated[str, Field(description="技能目录内的相对路径，如 references/ordering-workflow.md")],
) -> str:
    """读取已加载技能的参考模块（references/ 下的 Markdown）。只在命中对应意图时读，不要一次全读。"""
    skill = available().get(name)
    if skill is None:
        return f"没有名为「{name}」的技能"
    if path not in skill.files():  # 白名单：只能读 references/*.md，挡住 ../ 越界与任意文件读取
        return f"没有该文件。可读：{skill.files()}"
    text = (skill.root / path).read_text(encoding="utf-8")
    return text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS] + "…（已截断）"


SKILL_TOOLS = [load_skill, read_skill_file]
