# 问题排查记录

## 启动检查（Windows PowerShell）

完整步骤见 [README](../README.md)。后端与前端应分别在两个终端运行：

```powershell
# 项目根目录，终端一
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\langgraph.exe dev --no-browser --host 127.0.0.1 --port 2024
```

```powershell
# 项目根目录，终端二
Set-Location web
pnpm dev --hostname 127.0.0.1 --port 3000
```

可用 `Invoke-WebRequest http://127.0.0.1:2024/info` 检查后端，用浏览器访问 [http://localhost:3000](http://localhost:3000) 检查前端。这些检查不证明云端模型当前可用。

## 前端连接失败或找不到图

- 确认后端已启动，并且 `/info` 返回 200。
- `web/.env` 使用 `NEXT_PUBLIC_API_URL=http://localhost:2024` 和 `NEXT_PUBLIC_ASSISTANT_ID=cooker`；修改后重启前端。
- 未配置环境变量时，连接表单中的 Graph ID 要改为 `cooker`，不能保留模板默认的 `agent`。本地 LangSmith Key 留空。
- 从另一台设备访问时，localhost 指向访问者自己的设备。当前文档默认仅本机使用。

## 端口被占用

先检查占用进程，不要直接结束未知服务：

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 2024,3000 |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

若改用其它后端端口，需要同步修改前端 API URL 并重启前端。

## 数据缺失、索引不匹配或换算不可用

- 缺少 recipes.json / tips.json：先完整运行 cooker-ingest。
- 索引缺失或 embedding 签名变化：运行 cooker-index。
- 刚更新上游或重新入库：同步重建索引并重启进程，避免旧缓存与新数据混用。
- 使用 --no-llm、--limit 或 --only 后部分菜无法换算：检查 ingest_report.json 的 with_facts；这些命令仍会重写完整菜谱文件。恢复时完整入库，再建索引。
- 不要通过删除整个 data 目录排障，其中包含 CLI 的会话与偏好。备份方式见 [使用与维护](usage.md)。

## PowerShell 运行 pnpm lint 报环境变量命令错误

现有 lint 脚本使用 POSIX 赋值语法；PowerShell 等价命令见 [前端说明](../web/README.md#开发命令)。

## 页面首次编译较慢或字体下载失败

首次访问会触发 Next.js 编译，等待终端显示请求完成。项目通过 next/font/google 加载 Inter，网络不可用时检查字体下载日志；必要时改用本地字体。不要把 API 连接失败和前端编译失败混为一谈。

## 控制台中文乱码（`���`）

- **现象**：模型返回内容在终端显示为 `���`。
- **定位**：打印码位 `[hex(ord(c)) for c in content]` 得到 `0x4f60 0x597d`（即「你好」）——数据正确，是 Windows 控制台 GBK 解码 UTF-8 输出。
- **解决**：运行前设置 `PYTHONUTF8=1`。

## DeepSeek 返回空 content

- **现象**：`max_tokens=10` 时 DeepSeek（百炼托管）`content == ''`。
- **原因**：默认开启思考模式，token 全用在 `reasoning_content` 上。
- **解决**：`role_options.*.thinking: false` → 工厂发送 `extra_body={"enable_thinking": false}`。

## langchain-qwq 思考模式判断依赖地址字符串

- `ChatQwen._check_need_stream` 以 `"dashscope" in api_base` 判断是否强制流式；百炼工作空间地址为 `*.maas.aliyuncs.com`，不含该字符串。
- 当前所有角色关闭思考模式，不受影响。**若开启 thinking，需改用流式调用或验证非流式是否报错。**

## uv 写入了用户目录

- `uv python install` 默认在 `~/.local/bin` 生成 `python3.12.exe`。已删除，并在 `scripts/uv` 中设置 `UV_PYTHON_INSTALL_BIN=0`。

## GitHub 下载超时

- 直接从 GitHub Releases 下载 uv 超时。改为 `pip install uv --target tools/uv-pkg -i 阿里云镜像`。

## 过敏原大面积误标

- 见 technical-decisions.md TD-5。排查方法：统计标签分布 `Counter(...)`，再抽查不可能含该过敏原的品类（饮料）。

## 百炼接口突然 403 / Arrearage（2026-10-16）

- **现象**：建索引成功约 1 小时后，`qwen3.7-text-embedding` 返回 `403 AccessDenied.Unpurchased`，rerank 返回 `400 Arrearage`；`text-embedding-v4`、`qwen-flash`、`qwen-plus` 仍正常。
- **定位**：`scripts/dev/probe_access.py` 逐个模型探测，区分「个别模型无权限」和「整体网络 / 密钥问题」。
- **推测（未证实）**：免费额度用尽或账户欠费，需在百炼控制台确认。
- **工程改进**：熔断 + 降级统计（TD-11）。

## 检索评测结果全部相同

- **现象**：bm25 / hybrid / hybrid+rerank 指标完全一致，vector 全为 0。
- **原因**：向量和重排调用全部失败，实际只跑了 BM25。
- **改进**：评测脚本检查 `retriever.stats`，发生降级时退出码为 1，并标记结果作废。

## MCP 工具没有 structured_content

- 工具函数没写返回类型注解 → FastMCP 不生成 output schema → 只有文本内容。给所有工具加上返回类型即可。

## jieba SyntaxWarning

- jieba 0.42 源码中有无效转义序列，Python 3.12 首次编译时报 SyntaxWarning；在 `retriever.py` 中导入时屏蔽。

## 熔断器测试偶发失败

- **现象**：全量测试偶尔 1 个失败，重跑又通过。
- **定位**：单独循环 300 次 → 41 次失败；`time.get_clock_info('monotonic').resolution` = 0.015625（Windows 约 15.6ms），而测试里 cooldown 0.05s 与 sleep 0.06s 只差 10ms。
- **解决**：`CircuitBreaker` 接受可注入的 `clock`，测试用假时钟推进时间，不再 sleep。原则：**依赖真实时间的测试不可靠，时间应作为依赖注入**（类比 Java 的 `java.time.Clock`）。

## SQLite 事务冲突：cannot commit / cannot start a transaction within a transaction

- **现象**：CLI 第一次写档案就报错；离线测试全部通过（因为测试用的是 InMemorySaver / InMemoryStore）。
- **第一次判断（不完整）**：saver 和 store 共用一个连接，事务交错。改成两个连接后仍报错。
- **根因**：看了 `SqliteStore.from_conn_string` 源码——它用 `isolation_level=None`（autocommit），因为自己执行显式 `BEGIN`；
  Python sqlite3 默认会在 DML 前隐式开事务，再显式 `BEGIN` 就冲突。`SqliteSaver` 则相反，用默认模式、自己 `commit()`。
- **解决**：`cli._connect(db, autocommit)`：两个连接，各自使用与官方 `from_conn_string` 一致的模式；开启 WAL。
- **教训**：① 绕过官方工厂方法手动构造对象时，要对照它的源码复现全部参数；② 内存版替身测不出持久化层的问题，
  已补充真实 SQLite 的集成测试 `test_sqlite_persistence_end_to_end`。

## langchain-qwq 静默把 tool_choice="required" 改成 "auto"

- **现象**：主备模型绑定后打印 `tool_choice`：DeepSeek 是 required，ChatQwen 是 auto。
- **定位**：`langchain_qwq/base.py` 的 `bind_tools` 中 `if tool_choice == "required": tool_choice = "auto"`；
  用 openai SDK 直连百炼验证接口本身支持 required（对「你好」也强制调用了工具）。
- **解决**：`bind_tools(TOOLS).bind(tool_choice="required")` 绕过；加回归测试，升级依赖后能立刻发现。
- **教训**：对关键参数，打印最终发出的请求参数，不要假设封装库原样透传。

## Git Bash 下 curl 发送中文 JSON 返回 422

- `curl -d '{"content":"中文"}'` 在 Git Bash 中编码被破坏，服务端报 Invalid JSON。把请求体写入 UTF-8 文件后 `--data-binary @file` 即可。
