# COOKer · 个人做饭助手

基于 [HowToCook](https://github.com/Anduin2017/HowToCook) 的本地部署 Agent：根据现有食材选菜、查询原文做法、换算人数和生成购物清单。当前数据快照包含 372 道菜及烹饪技巧，数量随上游更新变化。

提供 Web 聊天、终端聊天和 MCP 工具。应用与数据保存在本机，聊天、结构化抽取、向量化和重排使用云端 API，因此不是完全离线应用。

## 功能

| 需求 | 示例 | 实现 |
|---|---|---|
| 按食材推荐 | 我有土豆、鸡蛋和番茄，能做什么？ | 主料匹配、缺料提示、多样性排序 |
| 查询做法 | 可乐鸡翅怎么做？ | 原文步骤、可点击菜谱抽屉 |
| 按条件选菜 | 推荐 30 分钟内能做的简单菜 | 结构化筛选、BM25 + 向量 + RRF + 重排 |
| 换算用量 | 西红柿炒鸡蛋做 4 人份需要多少材料？ | 程序计算结构化用量 |
| 购物清单 | 西红柿炒鸡蛋和可乐鸡翅，都做 3 人份 | 同名同单位合并、Web 勾选清单 |
| 保存偏好 | 我不吃香菜，家里三口人 | 跨会话档案，移除已记录过敏原需确认 |
| 查询技巧 | 焯水用冷水还是热水？ | 烹饪技巧检索 |

技术栈：Python 3.12、LangGraph、LangChain、FastMCP、numpy、jieba / rank-bm25；前端使用 Next.js、React 和 LangGraph SDK，基于 agent-chat-ui 改造。

## 首次安装（Windows PowerShell）

准备 Git、uv、Node.js 22 LTS 和 pnpm。Python 限定为 3.12，可由 uv 管理。前端声明使用 pnpm 10.5.1，建议使用该版本并保留锁文件。

```powershell
git clone https://github.com/Bryce-border/COOKer-agent.git
Set-Location COOKer-agent
git clone https://github.com/Anduin2017/HowToCook.git
uv sync --locked
$env:PYTHONUTF8 = "1"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

如果已将 uv 安装到 `tools/uv/uv.exe`，可以用 `./scripts/uv.cmd sync --locked` 替代 `uv sync --locked`。包装脚本依赖该可执行文件，不会自动寻找全局 uv。

编辑根目录 `.env`，填写 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`。模型名称及接入点位于 [config/models.yaml](config/models.yaml)，账户需具备对应模型的访问权限。

默认 Qwen 和 DeepSeek 都通过百炼接入。若要使用官方 DeepSeek，需要把其 provider 的 `endpoint` 改为 `deepseek_official`，并填写对应的密钥与地址。向量和重排仍使用百炼。

首次准备数据，每步成功后继续下一步：

```powershell
.\.venv\Scripts\cooker-ingest.exe
.\.venv\Scripts\cooker-index.exe
```

首次全量抽取会调用云端模型，历史规模约 100 万 token；实际费用取决于模型及重试次数。后续相同原文和 Prompt 版本会复用缓存。已有菜谱数据及对应索引时，日常启动无需重复入库。

## 启动 Web

需要两个终端，均从项目根目录开始。

终端一：启动后端。

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\langgraph.exe dev --no-browser --host 127.0.0.1 --port 2024
```

终端二：安装并启动前端。

```powershell
Set-Location web
pnpm install --frozen-lockfile
if (!(Test-Path .env)) { Copy-Item .env.example .env }
pnpm dev --hostname 127.0.0.1 --port 3000
```

`web/.env` 的本地配置：

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:2024
NEXT_PUBLIC_ASSISTANT_ID=cooker
```

打开 [http://localhost:3000](http://localhost:3000)。本地服务不需要 LangSmith API Key。若出现连接表单，填写后端地址和 Graph ID **cooker**；模板默认的 `agent` 不适用于本项目。

点击回答中的菜名打开原文抽屉；右上角切换模型；购物清单可勾选；移除过敏原时确认或取消。使用 Ctrl+C 分别停止两个终端。更多说明见 [前端 README](web/README.md)。

## 终端聊天与 MCP

CLI 无需启动 Web 或 LangGraph HTTP 服务：

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\cooker-chat.exe
# 可选：指定模型与档案
.\.venv\Scripts\cooker-chat.exe --provider deepseek --user alice
# 续聊时将占位符替换为启动时显示的会话 ID
.\.venv\Scripts\cooker-chat.exe --thread <会话ID>
```

命令：`/new` 新会话、`/profile` 查看偏好、`/provider qwen` 或 `/provider deepseek` 切换模型、`/quit` 退出。新会话会保留同一用户的长期偏好。

MCP 客户端配置示例，请替换本机绝对路径：

```json
{
  "mcpServers": {
    "cooker": {
      "command": "F:/project/COOKer-agent/.venv/Scripts/cooker-mcp.exe",
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

默认使用 stdio，由客户端启动进程。也可手动运行：

```powershell
.\.venv\Scripts\cooker-mcp.exe --http --host 127.0.0.1 --port 8765
```

MCP 提供 7 个菜谱工具，不包含本项目 Agent 的档案读写和过敏原移除确认；排除条件需调用方显式传入。

## 更新数据与模型

停止项目服务后，从项目根目录逐步运行，每步成功后继续：

```powershell
git -C HowToCook pull --ff-only
.\.venv\Scripts\cooker-ingest.exe
.\.venv\Scripts\cooker-index.exe
```

随后重启服务。数据和模型配置在进程中有缓存；更换 embedding 模型或维度也必须重建索引。

`--limit` / `--only` 只限制抽取目标，仍会重写完整菜谱文件；非目标条目无有效缓存时 facts 为空。`--no-llm` 会输出不含 facts 的数据。调试这些选项时使用独立的 `COOKER_DATA_DIR`，详见 [使用与维护](docs/usage.md)。

## 能力边界

- 当前面向单用户本地使用。Web 请求默认共用 `local` 档案，新会话不等于新用户。公开部署前需加入认证、线程与 Store 权限隔离和限流。
- 过敏原由关键词推导，可能漏标；“鱼”和“海鲜”是独立标签。过滤只对已有标记生效，仍需核对原料表。点名查菜会返回冲突提示。
- 用量计算是确定性的，但输入来自 LLM 抽取，应以原文为准。耗时可能是估算值；厨具和厨艺会进入提示词，目前不是硬过滤条件。
- 回答校验检查菜名与工具结果的关联，不验证全部步骤、数字和描述。流式生成期间可能短暂显示尚未通过校验的草稿。
- 向量或重排调用失败时可以降级，但启动仍需有效配置和本地数据。

## 测试与评测

从项目根目录运行：

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest -m live
.\.venv\Scripts\python.exe eval/run_retrieval_eval.py
.\.venv\Scripts\python.exe eval/run_agent_eval.py --repeat 3
```

默认 pytest 不调用真实模型，依赖本地菜谱的数据测试在文件缺失时会跳过。`live` 和两个评测脚本调用真实 API，会产生费用。检索评测遇到降级会判定结果无效。

已有记录：31 条检索样本中混合检索加重排 Hit@1 为 1.00；12 个 Agent 场景各重复 3 次，两个模型均曾达到 36/36。这些是自写小样本的检索和工具行为指标，不代表全面的回答准确率。见 [技术决策](docs/technical-decisions.md)。

## 文档与目录

- [使用与维护](docs/usage.md)：配置、更新、备份和数据生命周期。
- [架构](docs/architecture.md)：模块职责与检索流程。
- [核心链路](docs/core-flows.md)：对话、中断与记忆。
- [技术决策](docs/technical-decisions.md)：选型依据和评测。
- [问题排查](docs/troubleshooting.md)：启动与模型问题。
- [前端说明](web/README.md)：Web 操作和开发命令。

`src/cooker/` 为后端，`web/` 为前端，`config/` 为模型配置，`tests/` 与 `eval/` 为测试和评测。`HowToCook/` 是独立上游仓库；`data/` 保存生成物及 CLI 会话；`.langgraph_api/` 保存本地开发服务状态，三者不随 Git 提交。

## 数据与第三方许可

菜谱来自 HowToCook（Unlicense），不随本仓库分发。前端基于 [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui)，保留其 MIT 许可，见 [web/LICENSE](web/LICENSE)。
