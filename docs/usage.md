# 使用与维护

首次安装和日常启动以 [项目 README](../README.md) 为准。本文说明配置与数据生命周期，命令默认在项目根目录的 PowerShell 中运行。

## 选择入口

| 入口 | 启动方式 | 记忆 |
|---|---|---|
| Web | LangGraph Server + Next.js | Server 管理；默认用户 local |
| CLI | cooker-chat | data/agent.db |
| MCP | 客户端启动 cooker-mcp 或手动启动 HTTP 模式 | 仅菜谱工具，不含项目 Agent 的偏好读写 |

CLI 与 Server 不自动互通历史和偏好。CLI 的 --user 选择档案，--thread 选择会话；这些本地标识不是认证机制。

## 配置

| 配置 | 位置 | 说明 |
|---|---|---|
| API 密钥和地址 | 根目录 .env | 默认两种聊天模型均走百炼 |
| 模型名、超时和备用模型 | config/models.yaml | 按角色创建模型 |
| 默认厂商 | COOKER_PROVIDER | 运行上下文可覆盖 |
| 上游目录 | COOKER_HOWTOCOOK_DIR | 默认项目内 HowToCook |
| 数据目录 | COOKER_DATA_DIR | 默认 data，建议使用绝对路径 |
| 模型配置路径 | COOKER_MODELS_CONFIG | 默认 config/models.yaml |
| 前端连接 | web/.env | 后端 URL 和 Graph ID cooker |

切换聊天 provider 不切换 embedding / rerank。router、planner 角色目前未用于执行链路。模型配置有进程缓存，修改后重启服务。

## 入库与索引

常规更新按“停止服务 → 更新上游 → 完整入库 → 构建索引 → 重启服务”执行。检查退出状态及 data/ingest_report.json 的 parse_errors、extract_failed、with_facts。入库失败时仍可能已经写出部分结果，不应当作成功更新。

索引加载校验 embedding 签名及块/向量行数，但不验证与当前 recipes.json 的内容版本一致。每次菜谱重建后都应同步重建索引。

抽取缓存以原文哈希和 Prompt 版本为键；只切换抽取模型不会使缓存失效。向量缓存按模型签名和块文本哈希复用。

--only / --limit 只限制本次抽取，最终仍输出全部菜谱；非目标条目从缓存补齐，无缓存则 facts 为空。--no-llm 不复用 facts，原文查询仍可用，但推荐、换算和依赖 facts 的筛选会受影响。

使用独立目录做小规模调试：

```powershell
$previousDataDir = $env:COOKER_DATA_DIR
try {
    $env:COOKER_DATA_DIR = Join-Path (Get-Location) 'data/debug-ingest'
    .\.venv\Scripts\cooker-ingest.exe --limit 5
} finally {
    $env:COOKER_DATA_DIR = $previousDataDir
}
```

此示例仍调用云端抽取 API，但不覆盖默认数据文件。

## 备份

| 路径 | 内容 | 丢失影响 |
|---|---|---|
| data/recipes.json、tips.json | 解析与结构化结果 | 需重新入库 |
| data/cache/ | 抽取和向量缓存 | 重新生成可能产生费用 |
| data/index/ | 检索索引 | 需重建 |
| data/agent.db | CLI 会话与偏好 | 不能通过菜谱重建恢复 |
| .langgraph_api/ | 本地开发服务状态 | 清理可能丢失 Web 会话与偏好 |
| .env | 本机凭据 | 安全保管，不提交 Git |

关闭 CLI 和 Server 后再备份数据库及开发状态，避免复制正在写入的文件。data 同时包含可重建生成物和个人记录，不要为了重建索引删除整个目录。

## 评测解读

默认 pytest 通过假模型验证流程；部分 fixture 缺少本地菜谱会跳过，应同时关注通过数和跳过数。

检索评测为 25 条菜谱问题加 6 条技巧问题；Agent 评测为 12 个场景，每个模型重复 3 次即 36 次。Agent 主要核对工具选择、参数、档案写入和菜名依据，尚不能证明完整回答的用量、步骤或过敏判断正确。

报告写入 data/eval_retrieval.json、data/eval_agent.json，不随 Git 分发。历史指标和局限记录在 [技术决策](technical-decisions.md)。
