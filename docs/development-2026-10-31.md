# 开发记录 2026-10-31：麦当劳技能重新接入（P4）、聊天中显示菜品图片、瑞幸接入

更新：2026-10-31。旧版实现归档在分支 `archive/mcd-v1`，不合入 main。
逐步实现讲解见 `learning/02-麦当劳技能接入-逐步实现.md`；设计结论见 [技术决策](technical-decisions.md) TD-21、TD-22。

## 本次解决的问题

| 旧版问题 | 新做法 |
|---|---|
| SKILL.md 复制进来但没被读取，13 条手写规则每轮注入 | 技能按需加载：索引常驻，`load_skill` 后注入正文与工具，`read_skill_file` 读参考模块 |
| 写操作在独立表单页 `/mcd` | 对话内确认卡（interrupt），卡片数据来自实时查询 |
| 正则关键词判断要不要查麦当劳 | 同一个 ReAct 循环里由模型决定是否加载技能 |
| 同步工具里 `asyncio.run`、复制约 5000 行 mclottery | 同步 / 异步双入口；不复制计算层，新增后端约 600 行 |

## 改动清单

| 位置 | 内容 |
|---|---|
| `skills/mclottery-alchemist/` | 原样复制 SKILL.md、references/、LICENSE；来源与 commit 记录在 `skills/README.md` |
| `src/cooker/skills.py` | 技能发现、索引、`load_skill`（更新状态 `skills`）、`read_skill_file`（白名单） |
| `src/cooker/mcd/` | （同日接入瑞幸时，通用部分已移到 `src/cooker/connectors/`，见本文末节）`config.py` 密钥与官方端点；`client.py` 调用、白名单、结构校验、错误分级、分→元；`tool_schemas.json` 36 个工具快照；`tools.py` 生成 LangChain 工具；`actions.py` 确认卡与写操作；`ledger.py` 台账与解冻命令；`host_adapter.md` 宿主适配 |
| `src/cooker/agent/` | `graph.py` 新增 `AgentState.skills`，按已加载技能绑定工具；`prompts.py` 技能索引与正文；`verify.py` 麦当劳轮次跳过无括号菜名扫描；`cli.py` 终端确认卡 |
| `scripts/sync_mcd_tools.py` | 只调 `list_tools` 更新快照，提示未分类工具 |
| `web/` | `components/cooker/mcd-confirm.tsx` 确认卡；`ai.tsx` 接线；`lib/cooker.ts` 工具中文标签；`scripts/verify-mcd-confirm.mjs` 浏览器回归 |
| `tests/test_mcd.py` + `tests/fixtures/mcd/` | 17 个离线测试，内存假 MCP 服务 |
| `pyproject.toml` / `uv.lock` | 显式声明 `jsonschema`（此前只是传递依赖） |
| 文档 | README、architecture、core-flows、technical-decisions、troubleshooting、web/README；面试与简历材料移入 `learning/` |

## 开发中遇到的问题

1. **技能文档与真实服务不一致**：文档 33 个工具，服务端 36 个，4 处名称不同。用快照 + 调用前线上校验解决，差异写进宿主适配说明。
2. **旧台账表结构冲突**：`data/mcd-actions.sqlite3` 是旧版表结构，新代码报 `no such column`。确认其中 0 条执行记录后改用 `data/mcd-ledger.sqlite3`，旧文件保留未删。
3. **`langgraph dev` GBK 解码错误**：未设置 `PYTHONUTF8=1`，见 [问题排查](troubleshooting.md)。
4. **图外调用 interrupt 报错**：测试里直接调 `run_write` 走到 `interrupt()` 会报不在 runnable 上下文，改为替身函数模拟用户选择。

## 实际验证

| 项目 | 结果 |
|---|---|
| `pytest` | 78 通过（新增 17），2 个 live 默认跳过 |
| 前端 `tsc --noEmit`、`pnpm build` | 通过 |
| 做饭行为评测 `eval/run_agent_eval.py --repeat 1` | Qwen 12/12、DeepSeek 12/12，verify 介入 0 次，无误加载技能 |
| 真实模型 + 真实麦当劳（只读） | 查积分 / 抽奖正常；积分不足时回答「暂不可抽」；领券停在确认卡未确认 |
| 浏览器 `node scripts/verify-mcd-confirm.mjs` | 卡片出现 → 取消 → 工具返回 CANCELLED |
| `langgraph dev` 日志 | 无 blocking / 异常 |

**未验证**：真实账户上没有执行过任何写操作（下单、抽奖、兑换、领券、取消、新增地址），返回解析只由模拟数据覆盖。

## 已知局限

- 加载技能后每次模型调用多约 16.4k 字系统提示词 + 约 35k 字工具描述；技能整段会话保持激活。
- 台账只防护经过 COOKer 的调用，不覆盖手机 App 和其他客户端。
- 商城兑换卡片拿不到价格（下单参数只有 skuId），只提示「以下单返回为准」。
- 技能文档里的点餐、券、营养等字段仍有「待联调」项（见 `skills/.../references/tool-workflows.md` §12）。

## 下一步计划（P5，暂不实施，按收益排序）

| # | 事项 | 目的 | 大致做法 | 验证方式 |
|---|---|---|---|---|
| 1 | 技能路由评测 | 量化「该加载时加载、不该加载时不加载」及写操作行为 | `eval/` 增加麦当劳场景，用内存假服务，不花钱 | 场景 × 3 次通过率 |
| 2 | 工具分组绑定 | 降低加载后的 token 成本 | 按能力模块（点餐 / 优惠 / 积分抽奖 / 活动 / 营养）分组，`load_skill` 带模块参数或二级加载 | 对比字符数与评测通过率 |
| 3 | 技能卸载 | 转回做饭话题后不再携带麦当劳正文与工具 | `unload_skill` 工具，或若干轮未使用后自动卸载 | 单测 + 评测 |
| 4 | verify 检查描述忠实度 | 现在只查菜名存在性（P2 起的技术债） | 关键数字 / 食材与工具结果比对 | 新增反例评测 |
| 5 | 对话列表翻页 | 当前最多加载 100 条 | 前端分页或无限滚动 | 浏览器回归 |
| 6 | 冻结独立测试集 + 延迟分位数 | 评测结果可信、可对比 | 真实问题、长尾同义词、无答案问题；报告 P50/P95 | 评测报告 |
| 7 | 写操作真实联调 | 用小额、可取消的操作核对返回解析 | 本人在场，逐个工具执行一次，记录真实返回结构 | 更新契约文档与 fixtures |

---

# 聊天中显示菜品图片

逐步讲解见 `learning/03-聊天中显示菜品图片.md`；设计结论见 [技术决策](technical-decisions.md) TD-23。

## 解决的问题

菜品图片只在点开菜谱抽屉后才能看到。现在回答结束后，回答里提到的带图菜（【菜名】）会在回答下方显示成品图卡片，点击打开抽屉。372 道菜中 179 道有图。

## 改动清单

| 位置 | 内容 |
|---|---|
| `src/cooker/api.py` | `pick_cover()` 按文件名挑成品图；新增 `GET /cooker/covers?names=…`（批量、最多 8 个、无图与查不到的跳过）；详情接口把封面排第一张 |
| `web/src/lib/cooker.ts` | `citedRecipes()` 提取【菜名】；`fetchCovers()` 结果缓存 + 请求中去重 |
| `web/src/components/cooker/recipe-covers.tsx` | 回答下方的图片卡片，流式输出结束后才请求 |
| `web/src/components/thread/messages/ai.tsx` | 接线 |
| `tests/test_api.py` | 新增 4 个测试 |
| `web/scripts/verify-recipe-covers.mjs` | 浏览器回归（预置会话，不调用模型） |

## 遇到的问题

1. **第一张图常是步骤图**（如清蒸鲈鱼第一张是「改刀」）→ 按文件名打分选封面。
2. **同一批菜名请求两次**：开发模式 React 严格模式让 effect 执行两遍，缓存在响应后才写入 → 增加「请求中」去重，实测降为 1 次。
3. **预置会话出现「需要你确认 when: breakpoint」**：测试用 `asNode: "agent"` 写入状态，图认为还要执行 verify → 改为 `asNode: "verify"`。

## 验证

pytest 82 通过；前端 tsc、eslint 通过；浏览器回归通过；真实模型「推荐几道周末海鲜菜」显示 4 张成品图、封面请求 1 次。
**未验证**：本次未跑 `pnpm build`（3000 端口的开发服务占用 `.next`）。

## 局限

封面靠文件名猜，`1.jpg` 类命名无法判断；没有缩略图（原图中位数约 211 KB）。

---

# 瑞幸咖啡（my-coffee 技能）接入

逐步讲解见 `learning/04-瑞幸技能接入-逐步实现.md`；设计结论见 [技术决策](technical-decisions.md) TD-24。

## 解决的问题

聊天中可以点瑞幸：找门店 → 选商品与规格 → 系统确认卡（实时预览价格、优惠券）→ 生成支付二维码；也可以查订单、取消订单。只支持到店自取（技能约束）。

## 改动清单

| 位置 | 内容 |
|---|---|
| `src/cooker/connectors/` | **新增通用层**：base（Connector、凭据）、client、actions、ledger、tools，由原 `mcd/` 提取 |
| `src/cooker/mcd/connector.py` | 麦当劳只剩白名单、7 张确认卡、分→元；删除 mcd 的 client / actions / tools / config |
| `src/cooker/luckin/` | 瑞幸连接器（2 个写工具、6 个读工具）、`tool_schemas.json`、`host_adapter.md` |
| `skills/my-coffee/` | 原样复制 SKILL.md、LICENSE（CC BY-ND 4.0）、CHANGELOG、manifest |
| `config/luckin-mcp.example.env` | 配置模板；真实 Token 在 `luckin-mcp.local.env`（`.gitignore` 改为忽略 `/config/*.local.env`） |
| `scripts/sync_mcp_tools.py` | 原 `sync_mcd_tools.py`，按参数同步任一服务；去掉瑞幸结构里的私有字段 `returnDirect` |
| `src/cooker/skills.py`、`agent/verify.py`、`agent/cli.py` | 技能工具来自连接器注册表；中断类型改为 `action_confirm` |
| `web/` | `mcd-confirm.tsx` → `action-confirm.tsx`（按服务显示图标，「应付」标红）；瑞幸工具中文标签；`verify-mcd-confirm.mjs` → `verify-action-confirm.mjs [mcd|luckin]` |
| `tests/test_luckin.py` | 10 个离线测试；`test_mcd.py` 改用新接口 |
| `data/` | 台账 `mcd-ledger.sqlite3` 改名 `connector-ledger.sqlite3`（含 1 条真实记录，原样保留） |

## 遇到的问题

1. **台账里有一条真实 SUCCESS 记录**：11:12:59 的一键领券（成功 3 张），不是自动测试产生的，应为在网页上点了确认。说明写操作链路在真实账户上跑通过一次；改名时原样保留数据。
2. **瑞幸工具结构有私有字段 `returnDirect`**：不属于 JSON Schema，同步快照时去掉，避免模型接口拒收。
3. **技能假设宿主有 shell**：curl 调用、IP 定位、Token 落盘到 `~/.my-coffee/` 在 COOKer 中都不适用，写进宿主适配说明覆盖。
4. **晚上门店全部「打烊中」**：确认卡显示门店状态并提示；因此本次没有尝试真实下单。

## 验证

| 项目 | 结果 |
|---|---|
| pytest | 92 通过（新增瑞幸 10 个；麦当劳 17 个改接口后全部通过） |
| 前端 tsc、eslint | 通过 |
| 做饭评测 `--repeat 1` | Qwen 12/12、DeepSeek 12/12，无误加载技能 |
| 真实模型 + 真实瑞幸（只读） | 「人民广场附近点生椰拿铁」→ 加载 my-coffee → 查门店（未展示距离）→ 用户选店 → 查规格 → 调用 createOrder → 确认卡：原价 ¥20.00、减免 ¥0.00、应付 ¥20.00、提示打烊 |
| 路由 | 「麦当劳积分还有多少」只加载 mclottery-alchemist |
| 浏览器 `verify-action-confirm.mjs luckin` / `mcd` | 两个场景：确认卡出现 → 取消 → 返回 CANCELLED |

**未验证**：真实下单、支付二维码展示、真实订单查询与取消（需要营业时间、本人在场）；订单详情字段名。

## 下一步（并入 P5）

- 营业时间内本人在场做一次真实下单 → 查单 → 取消（未支付订单），核对订单详情字段与二维码展示。
- 浏览器定位：前端经用户授权取经纬度传给后端，替代模型对地名的粗略坐标。
- 技能路由评测加入瑞幸 / 麦当劳易混场景（如「帮我下单」「点一杯」）。
