# COOKer 架构

本地部署的个人做饭助手 Agent，数据来自 [HowToCook](https://github.com/Anduin2017/HowToCook)。

## 当前状态（P2 完成）

| 层 | 状态 | 位置 |
|---|---|---|
| 模型接入层 | ✅ 已实现 | `src/cooker/llm/factory.py` + `config/models.yaml` |
| 数据层（解析 + 抽取） | ✅ 已实现 | `src/cooker/ingest/` |
| 领域层（存储/过滤/换算/购物清单/按食材推荐） | ✅ 已实现 | `src/cooker/recipes/` |
| 检索层（BM25 + 向量 + RRF + 重排，带熔断降级） | ✅ 已实现，已评测（见 technical-decisions TD-14） | `src/cooker/retrieval/` |
| 服务层 | ✅ 已实现 | `src/cooker/service.py` |
| MCP 工具层（7 个工具，stdio / HTTP） | ✅ 已实现 | `src/cooker/mcp_server/server.py` |
| LangGraph Agent（ReAct + verify 校验 + 档案记忆 + 人在回路） | ✅ 已实现，已评测（TD-19） | `src/cooker/agent/` |
| 前端（agent-chat-ui） | ⏳ P3 计划 | — |

## 目录

```
COOKer-agent/
├── HowToCook/              上游菜谱仓库（只读，独立 git，gitignore）
├── config/models.yaml      模型配置：厂商 / 接入地址 / 角色 → 模型
├── src/cooker/
│   ├── settings.py         路径与全局配置（pydantic-settings，读 .env）
│   ├── llm/factory.py      按角色创建模型；主备降级
│   └── ingest/
│       ├── schema.py       数据模型：ParsedRecipe（规则）/ RecipeFacts（LLM）/ Recipe（合并）
│       ├── parser.py       Markdown → ParsedRecipe（规则解析，依赖上游 lint 约定）
│       ├── extractor.py    ParsedRecipe → RecipeFacts（LLM 抽取 + 哈希缓存 + 并发）
│       ├── allergens.py    过敏原规则推导
│       └── pipeline.py     CLI 入口 cooker-ingest
│   ├── llm/embedding.py    百炼向量化（分批 20 条、L2 归一化）与重排客户端
│   ├── recipes/
│   │   ├── store.py        内存菜谱库：按名称查找（同义归一）、结构化过滤 RecipeFilter
│   │   ├── ingredients.py  食材名归一化、常备调料表、菜名归一化
│   │   ├── scaling.py      按人数换算、购物清单合并（纯计算）
│   │   └── recommend.py    按现有食材推荐（覆盖度打分 + 多样性重排）
│   ├── retrieval/
│   │   ├── chunks.py       切块（带菜名前缀，去除模板话术）
│   │   ├── indexer.py      CLI cooker-index：向量化（哈希增量缓存）→ data/index/
│   │   └── retriever.py    HybridRetriever：BM25 + 向量 → RRF → 父子聚合 → 重排；CircuitBreaker
│   ├── service.py          CookerService：给 MCP / Agent 用的精简接口
│   ├── agent/
│   │   ├── graph.py        StateGraph：agent → tools / verify；get_agent_model（强制工具调用开关）
│   │   ├── tools.py        9 个 Agent 工具（ToolRuntime 读档案、强制过敏原过滤、interrupt 确认）
│   │   ├── verify.py       确定性回答校验：【菜名】引用必须有工具依据
│   │   ├── profile.py      用户档案（LangGraph Store）
│   │   ├── prompts.py      系统提示词（动态注入档案）
│   │   └── cli.py          cooker-chat（SQLite 持久化）
│   └── mcp_server/server.py  FastMCP 工具定义（cooker-mcp）
├── data/                   生成物（gitignore）：recipes.json / tips.json / ingest_report.json / cache/facts/
├── eval/                   检索消融评测、Agent 场景评测
├── langgraph.json          langgraph dev / Aegra 加载入口（图 cooker）
├── tests/                  pytest；-m live 运行真实 API 测试
├── scripts/uv(.cmd)        项目内 uv 包装（Python、缓存都在项目目录）
└── tools/                  uv 与 Python 解释器（gitignore）
```

## 数据入库流程

```
HowToCook/dishes/**/*.md
   │ parser.parse_recipe()            规则：标题/分类/难度/卡路里/图片/各节原文/步骤
   ▼
ParsedRecipe ──content_hash──► FactsCache.get()  命中 → 直接用
   │ 未命中
   ▼
extractor.extract_all()            asyncio + Semaphore(8) 并发
   │ build_runnable("extract", _structured)
   │   ChatQwen(qwen-flash).with_structured_output(RecipeFacts, function_calling)
   │     .with_retry(格式错误重试 2 次)
   │   .with_fallbacks([ChatDeepSeek(...) 同样包装])
   ▼
RecipeFacts（Pydantic 校验 + 标签归一化 + 单位归一化）──► FactsCache.put()（原子写）
   ▼
pipeline: Recipe = ParsedRecipe + facts + allergens(规则推导)
   ▼
data/recipes.json（372 道）、data/tips.json（18 篇）、data/ingest_report.json
```

## 运行

```bash
scripts/uv sync                       # 安装依赖到 .venv
.venv/Scripts/cooker-ingest           # 入库（增量）
.venv/Scripts/python -m pytest        # 单元测试
.venv/Scripts/python -m pytest -m live  # 真实 API 测试（Qwen + DeepSeek）
```

Windows 下建议设置 `PYTHONUTF8=1`，否则控制台中文可能乱码（数据本身无误）。

## 检索流程

```
search_recipes(query, 过滤条件)
  → RecipeStore.filter(RecipeFilter)          候选菜谱 ID 集合（内存遍历 372 条）
  → HybridRetriever.search(query, doc_ids)
      ├ BM25Okapi(jieba.cut_for_search)       top-50 块
      └ embed_query → vectors @ q（暴力余弦）  top-50 块     ← 熔断：失败 2 次停 60s
      → RRF(k=60) 融合 → 按 doc_id 聚合（取最佳块，保留 2 块作证据）→ top-20 文档
      → Reranker（概览 + 证据，≤1500 字）      ← 熔断：失败则保留 RRF 顺序
  → top_k SearchResult（摘要 + 片段）
```

## 接入 Claude Desktop / Cursor

```json
{"mcpServers": {"cooker": {
  "command": "F:/project/COOKer-agent/.venv/Scripts/cooker-mcp.exe",
  "env": {"PYTHONUTF8": "1"}
}}}
```

运行顺序：`cooker-ingest` → `cooker-index` → `cooker-mcp` / `cooker-chat` / `langgraph dev`。

## Agent 运行方式

```bash
.venv/Scripts/cooker-chat                  # 终端聊天，数据在 data/agent.db
.venv/Scripts/cooker-chat --provider deepseek
.venv/Scripts/langgraph dev --no-browser   # LangGraph Server：http://127.0.0.1:2024（P3 前端连接它）
```

Agent 链路详见 core-flows.md。
