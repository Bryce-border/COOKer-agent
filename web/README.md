# COOKer Web

基于 [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) 的 Next.js 聊天界面，连接 COOKer LangGraph 后端。完整安装步骤见 [项目 README](../README.md)。

## 本地启动（PowerShell）

先在项目根目录启动已准备好数据和模型配置的后端：

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\langgraph.exe dev --no-browser --host 127.0.0.1 --port 2024
```

在另一个终端进入 `web/`：

```powershell
pnpm install --frozen-lockfile
if (!(Test-Path .env)) { Copy-Item .env.example .env }
pnpm dev --hostname 127.0.0.1 --port 3000
```

建议使用 Node.js 22 LTS 和 package.json 指定的 pnpm 10.5.1。打开 [http://localhost:3000](http://localhost:3000)，修改环境文件后重启前端。

| 变量 | 本地值 | 用途 |
|---|---|---|
| NEXT_PUBLIC_API_URL | http://localhost:2024 | 浏览器可访问的后端地址 |
| NEXT_PUBLIC_ASSISTANT_ID | cooker | langgraph.json 中的图名称 |

未配置时会显示上游连接表单；Graph ID 填 `cooker`，本地服务的 LangSmith API Key 留空。API URL 环境变量优先于 URL 查询参数。

## 使用

- 输入自然语言或点击示例问题开始聊天。
- 点击菜名打开原文抽屉；输入人数后可发起换算提问。
- 右上角选择 Qwen / DeepSeek；历史列表可切换会话。
- 历史列表的删除按钮需要二次确认；失败会保留对话。删除当前对话后返回新对话，不清除长期偏好。
- 历史加载失败时点击「重新加载对话」；加载中及失败时暂停发送，避免向未恢复的会话继续提交。
- 工具调用显示中文摘要，展开查看详情；购物清单可勾选。
- 回答结束后，图片插到对应信息旁（`lib/inline-images.ts` rehype 插件；列表卡片图 128px、段落下方图片行 144px、表格 64px，商品图可点击放大）：列表项第一个提到的菜 / 商品有图时改为卡片（左侧大图），其余有图名称与段落、标题提到的名称放在下方带名称的图片行，表格在名称前插图。菜谱封面来自 `useRecipeCovers`（点击图片打开抽屉），商品图来自工具消息的 `artifact.images`；确认卡带缩略图。
- 过敏原移除请求暂停对话，确认或取消后恢复。
- 麦当劳、瑞幸写操作（下单、兑换、抽奖、领券、取消等）在对话中显示确认卡（`components/cooker/action-confirm.tsx`），确认时把卡片指纹带回后端；后端发现实时信息变化会拒绝执行。
- Web 会话默认共享 local 档案；CLI 的偏好保存在另一套存储中。

菜谱抽屉通过 `GET /cooker/recipes?name=...` 直接读取原文，不调用模型；换算按钮会提交聊天消息，因此会调用模型。图片由 `/cooker/images/...` 提供。

Next.js 已更新至 16.4.3。左下角 Next.js 开发工具用于查看路由、构建与错误信息，生产构建不显示。构建仍可能提示 LangGraph passthrough 的认证方式迁移建议，这与旧版本漏洞警告不同；当前未通过禁用日志隐藏提示。

## 开发命令

```powershell
pnpm build
pnpm start --hostname 127.0.0.1 --port 3000
pnpm exec tsc --noEmit
```

start 需要先成功 build。构建使用 next/font/google，可能需要联网下载字体。

现有 lint 脚本采用 POSIX 环境变量语法，PowerShell 可执行等价命令：

```powershell
$previousNodeOptions = $env:NODE_OPTIONS
try {
    $env:NODE_OPTIONS = '--require ./scripts/use-typescript6.cjs'
    pnpm exec eslint .
} finally {
    $env:NODE_OPTIONS = $previousNodeOptions
}
```

业务组件集中在 `src/components/cooker/`；`src/lib/cooker.ts` 负责菜名链接、详情请求和工具摘要；`src/providers/` 管理会话连接。

前后端启动后，使用本机 Edge 执行浏览器回归：

```powershell
node scripts/verify-recipe-covers.mjs # 菜谱配图位置，预置会话，不调用模型
node scripts/verify-product-images.mjs [--real|--real-mcd] # 商品图位置；默认预置会话不调用模型
node scripts/verify-action-confirm.mjs mcd     # 真实模型 + 麦当劳只读查询，确认卡只点「取消」
node scripts/verify-action-confirm.mjs luckin  # 同上，瑞幸下单确认卡
node scripts/verify-conversations.mjs
node scripts/verify-trace.mjs          # 回答下方「执行过程」汇总与展开
```

脚本创建并清理临时会话，包含一次真实模型问候请求。

## 部署边界

当前配置面向本地个人使用。公开部署需为后端增加认证、线程与 Store 权限隔离、限流，并从可信认证上下文获取用户身份。

仓库保留了上游 `src/app/api/[..._path]/route.ts` 代理。它本身不认证调用者，不能只配置 LANGGRAPH_API_URL 和 LANGSMITH_API_KEY 就直接开放访问。所有 NEXT_PUBLIC_* 变量都会公开给浏览器，不得存放服务端密钥。

远程受保护后端还需统一聊天 SDK、菜谱详情和图片请求的认证方式；目前详情 fetch 不附带聊天 SDK 的认证头。本地配置无需这些额外设置。

## 许可

上游 agent-chat-ui 使用 MIT 许可，版权声明保留于 [LICENSE](LICENSE)。
