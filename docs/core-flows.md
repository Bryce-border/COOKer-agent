# 核心链路

## 一轮对话（cooker-chat / LangGraph Server）

```
用户消息
  │  checkpointer 按 thread_id 恢复历史消息
  ▼
agent 节点（graph.call_model）
  │ 1. load_profile(store, user_id)       长期记忆 → 写进系统提示词
  │ 2. trim_messages(≤12k token, 从用户消息开始)
  │ 3. get_agent_model(provider, require_tool)
  │      = bind_tools(TOOLS) [+ tool_choice=required] → with_fallbacks(DeepSeek)
  ▼
route_after_agent
  ├─ 有 tool_calls → tools 节点（ToolNode，并行执行）
  │     每个工具通过 ToolRuntime 拿到 store / context
  │     检索类工具：_filter() 把档案里的过敏原、忌口强制并入 RecipeFilter
  │     update_profile 移除过敏原 → interrupt() 暂停，等用户确认
  │     → 回到 agent
  └─ 无 tool_calls → verify 节点（确定性校验）
        通过 → END
        不通过且重答 < 2 次 → RemoveMessage(这版回答) + 隐藏的校验反馈 → agent
          缺少查询依据时强制调工具；仅库外菜名时允许改写
        不通过且已重答 2 次 → 回答末尾追加「未能与菜谱库核对」→ END
```

## 人在回路（移除过敏原）

```
update_profile(remove_allergies=[花生])
  → interrupt({"type":"confirm", question})      图暂停，状态写入 checkpointer
  → 前端 / CLI 读取 state.interrupts，询问用户
  → graph.invoke(Command(resume="是"), 同一 thread_id)
  → update_profile 从头重新执行，interrupt() 这次返回 "是" → 写入 store
```

要点：恢复时工具会**从头重新执行**，所以 interrupt 之前不能有写操作。

## 数据从哪来

| 数据 | 存储 | 生命周期 |
|---|---|---|
| 菜谱、技巧 | `data/recipes.json`、`tips.json`（内存常驻） | `cooker-ingest` 重建 |
| 检索索引 | `data/index/`（numpy 向量 + 块） | `cooker-index` 重建 |
| 会话历史、中断状态 | checkpointer：CLI 用 `data/agent.db`，Server 由平台管理 | 按 thread_id |
| 用户档案 | store：namespace `("users", user_id)`，key `profile` | 同一存储内跨会话保留；依赖底层数据持久化与备份 |

CLI 使用 `data/agent.db`；本地 LangGraph Server 使用其开发运行时管理的状态（`.langgraph_api/`），两套记忆不自动同步。Web 当前未传 user_id，使用默认的 `local` 档案；新建会话不会清空偏好。

## Web 操作链路

普通消息由 LangGraph SDK 提交，运行上下文传入模型 provider。菜名引用转为可点击按钮，打开抽屉后通过 `GET /cooker/recipes?name=...` 读取原文，本地图片走 `/cooker/images/...`。这些读取不调用 LLM。

抽屉中的换算按钮会发送新的聊天消息，由 Agent 调用换算工具；过敏原确认按钮通过 `Command(resume=...)` 恢复同一会话。完整启动步骤见 [README](../README.md)。
