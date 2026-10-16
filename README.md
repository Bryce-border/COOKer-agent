# COOKer

本地部署的个人做饭助手 Agent，基于 [HowToCook](https://github.com/Anduin2017/HowToCook) 菜谱库（372 道家常菜 + 烹饪技巧）。

- **问菜谱**：混合检索（BM25 + 向量 + RRF + 重排），回答以原文为准
- **按现有食材推荐**、**按人数换算用量**、**合并购物清单**（确定性计算，不让 LLM 心算）
- **记住偏好**：过敏原、忌口、人数、厨具；过敏原由代码强制过滤，移除需用户确认
- **防幻觉**：确定性校验节点检查回答引用的菜是否有工具依据，不通过则重答
- 多模型：Qwen（默认）/ DeepSeek，配置切换，主备自动降级
- 同时提供 MCP Server，可接入 Claude Desktop / Cursor

技术栈：Python 3.12 · LangGraph · LangChain · FastMCP · numpy · jieba / rank-bm25 · 阿里云百炼

## 快速开始（Windows）

```bash
git clone https://github.com/Bryce-border/COOKer-agent.git && cd COOKer-agent
git clone https://github.com/Anduin2017/HowToCook.git      # 菜谱数据（只读）
# 安装 uv 到 tools/uv（或使用已安装的 uv），然后：
scripts/uv sync
cp .env.example .env                                        # 填写 DASHSCOPE_API_KEY 等
.venv/Scripts/cooker-ingest     # 解析菜谱 + LLM 结构化抽取（约 100 万 token，结果有缓存）
.venv/Scripts/cooker-index      # 构建检索索引
.venv/Scripts/cooker-chat       # 终端聊天
```

其它入口：`cooker-mcp`（MCP Server）、`langgraph dev`（LangGraph Server，供前端连接）。

## 测试与评测

```bash
.venv/Scripts/python -m pytest              # 离线测试
.venv/Scripts/python -m pytest -m live      # 真实 API 测试
.venv/Scripts/python eval/run_retrieval_eval.py
.venv/Scripts/python eval/run_agent_eval.py --repeat 3
```

## 文档

- [架构](docs/architecture.md) · [核心链路](docs/core-flows.md) · [技术决策](docs/technical-decisions.md) · [问题排查](docs/troubleshooting.md)

## 许可

菜谱数据来自 HowToCook（Unlicense），不随本仓库分发。
