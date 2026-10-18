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
- 工具调用显示中文摘要，展开查看详情；购物清单可勾选。
- 过敏原移除请求暂停对话，确认或取消后恢复。
- Web 会话默认共享 local 档案；CLI 的偏好保存在另一套存储中。

菜谱抽屉通过 `GET /cooker/recipes?name=...` 直接读取原文，不调用模型；换算按钮会提交聊天消息，因此会调用模型。图片由 `/cooker/images/...` 提供。

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

## 部署边界

当前配置面向本地个人使用。公开部署需为后端增加认证、线程与 Store 权限隔离、限流，并从可信认证上下文获取用户身份。

仓库保留了上游 `src/app/api/[..._path]/route.ts` 代理。它本身不认证调用者，不能只配置 LANGGRAPH_API_URL 和 LANGSMITH_API_KEY 就直接开放访问。所有 NEXT_PUBLIC_* 变量都会公开给浏览器，不得存放服务端密钥。

远程受保护后端还需统一聊天 SDK、菜谱详情和图片请求的认证方式；目前详情 fetch 不附带聊天 SDK 的认证头。本地配置无需这些额外设置。

## 许可

上游 agent-chat-ui 使用 MIT 许可，版权声明保留于 [LICENSE](LICENSE)。
