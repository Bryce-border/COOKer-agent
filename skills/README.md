# skills/

COOKer 按需加载的 Agent Skill（渐进式披露）：系统提示词只放每个技能的 name + description，
模型调用 `load_skill` 后才注入 SKILL.md 正文，按需用 `read_skill_file` 读 references/。
加载逻辑见 `src/cooker/skills.py`。

| 技能 | 来源 | 说明 |
|---|---|---|
| `mclottery-alchemist/` | F:/project/MC/mclottery-alchemist（commit 2d3957a）的 `skills/mclottery-alchemist/` | 原样复制 SKILL.md 与 references/，未改动；未复制 assets/demo.html（离线工作台）与 tests/（行为用例） |

**不要直接改技能正文。** 与 COOKer 宿主相关的差异（工具名、确认卡、无工作台等）写在宿主适配说明
`src/cooker/mcd/host_adapter.md`，加载技能时附加在正文之后。上游更新后重新复制即可。
