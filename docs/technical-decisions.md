# 技术决策记录

每条：背景 → 决策 → 备选 → 代价 / 何时重新考虑。

## TD-1 多厂商模型接入：配置驱动 + 按角色取模型

- **背景**：需要同时支持 Qwen（默认）和 DeepSeek；两家模型名在 2026 年都在变（DeepSeek 停用 `deepseek-chat`，Qwen 别名滚动更新）。
- **决策**：`config/models.yaml` 分三层：`endpoints`（地址 + 密钥变量名）→ `providers`（适配器 + 角色→模型 ID）→ `role_options`（温度、思考模式、超时）。代码只调用 `get_chat_model(role)` / `build_runnable(role, transform)`。
- **适配器**：Qwen 用 `langchain-qwq.ChatQwen`，DeepSeek 用 `langchain-deepseek.ChatDeepSeek`（处理 `reasoning_content`），其它兼容厂商用 `ChatOpenAI`。
- **备选**：① 全部用 `ChatOpenAI + base_url`——最简单，但丢失厂商特有字段（思考内容）；② LiteLLM 网关——功能多（成本统计、限流），但多一个进程，对单用户本地部署过重。
- **何时重新考虑**：接入厂商超过 4~5 家、或需要统一计费限流时，引入 LiteLLM。

## TD-2 降级顺序：transform → retry → fallback

- `RunnableWithFallbacks` 没有 `bind_tools`，所以 `with_structured_output` / `bind_tools` 必须在 `with_fallbacks` 之前分别作用于主备模型（`build_runnable` 的 `transform` 参数）。
- 格式错误先在同厂商内 `with_retry` 重试，再降级。否则一次 JSON 不合规就切厂商，浪费且掩盖 Prompt 问题。
- 实测全量 372 次调用中有 1 次走了 DeepSeek 降级。

## TD-3 解析分两层：规则解析 + LLM 抽取

- **背景**：上游 `manual_lint.js` 在 CI 中强制格式（4 个固定二级标题、难度/卡路里格式、操作用有序列表），但「计算」一节写法高度自由（每人量 / 每份量 / 公式 / 基础量）。
- **决策**：能确定性解析的字段用规则（`parser.py`），违反约定直接抛 `RecipeFormatError`；只有用量、口味、烹饪方式等语义字段交给 LLM（`extractor.py`）。原文完整保留，回答时以原文为准。
- **备选**：全部交给 LLM——简单但不可复现、成本高、可能篡改确定性字段。
- **代价**：上游改格式时 parser 会报错——这是有意为之（fail fast），由 `test_all_upstream_recipes_parse` 回归测试兜底。

## TD-4 LLM 输出的后处理放在 Pydantic 校验器里

- 标签：Schema 用 `Literal` 枚举（进入 function calling 的 JSON Schema），校验器再把近义词映射回词表、丢弃未知值，而不是让整条记录失败。
- 用量：`amount=None` 一律 `to_taste`；kg/斤/升 换算到 g/ml。
- 好处：确定性规则比改 Prompt 可靠；且读旧缓存时同样经过校验器，修规则不需要重新调用 LLM。

## TD-5 过敏原改为规则推导，不用 LLM

- **背景**：facts-v2 由 LLM 判断过敏原，实测 351/372 道菜被标「麸质」（冬瓜茶也被标了大豆、麸质）——字段描述里的「酱油含大豆和麸质」提示导致过度泛化。
- **决策**：`allergens.py` 用「过敏原 → 关键词」词典 + 排除词（鱼露、鱿鱼≠鱼）匹配食材名与原料原文。
- **代价**：词典外食材会漏标。上层回答必须提示「以原料表为准」，不能做安全保证。

## TD-6 抽取缓存：内容哈希 + Prompt 版本，一菜一文件

- 键 = `sha256(PROMPT_VERSION + 原文 sha256)`。原文或 Prompt 变了才重新抽取；上游 `git pull` 后只处理改动的菜谱。
- 一菜一个 JSON，写入用临时文件 + `replace` 原子替换，中断不会留下半个文件。
- 备选 SQLite：查询更强，但当前只需按键读写，文件足够；P1 会把最终数据导入 SQLite 供检索。

## TD-7 工具链放在项目目录

- uv 从阿里云 PyPI 镜像装到 `tools/uv`；Python 由 uv 装到 `tools/python`（npmmirror 镜像）；依赖缓存在 `.cache/uv`。
- `UV_PYTHON_INSTALL_DIR` 等只能用环境变量设置，故用 `scripts/uv` / `scripts/uv.cmd` 包装。
- `UV_PYTHON_INSTALL_BIN=0`：防止 uv 往 `~/.local/bin` 写 `python3.12.exe`。

## TD-8 不用向量数据库：numpy 暴力检索 + 内存过滤

- **背景**：1731 个块 × 1024 维 float32 ≈ 7MB；结构化过滤对象只有 372 条。
- **决策**：向量存 `vectors.npy`，查询时一次矩阵乘法；过滤在内存遍历。原计划的 SQLite、LanceDB 都去掉。
- **原理**：ANN 索引（HNSW / IVF）用召回精度换速度。数据量小时暴力检索既最准（精确 top-k）又足够快（毫秒级，查询耗时主要在网络调用）。
- **何时重新考虑**：块数到十万级、需要多进程共享或在线增删时，迁移到 LanceDB / Milvus。`HybridRetriever` 只依赖 `_vector_rank` 一个方法，替换面很小。

## TD-9 BM25 + 向量混合，用 RRF 融合

- 菜名直查（「宫保鸡丁」）是关键词强项；描述性需求（「用啤酒炖的鸭子」）是向量强项。
- **RRF**：`score = Σ 1/(60 + rank)`。只用名次不用原始分数，不需要把 BM25 分数（无上界）和余弦相似度（-1~1）归一到同一量纲。
- **备选**：加权分数相加——需要调权重、且分数分布随查询变化，不稳定。
- jieba 加入菜名与食材名作自定义词，`cut_for_search` 同时产出整词和子词；词典缓存目录改到项目 `.cache/jieba`。

## TD-10 父子检索：按块召回、按菜返回

- 小块（单组步骤）召回更准；但用户要的是「一道菜」。块分数按 `doc_id` 取最高分聚合，保留 2 个最佳块作为证据片段。
- 每块带「【菜名】小节名」前缀（contextual chunk header），否则步骤块单独看不出属于哪道菜。
- 切块时去掉上游每篇都有的模板话术（57 个块只有这一句，属于纯噪声）。

## TD-11 外部依赖的降级与熔断

- 向量失败 → 只用 BM25；重排失败 → 保留 RRF 顺序。检索不因外部服务故障而失败。
- `CircuitBreaker`：连续失败 2 次断开 60 秒，期间不请求；到期放行试探请求（半开）。
- 来源：实际故障中（embedding 403、rerank 欠费），没有熔断时每次查询都白等一次失败请求，评测打出了 188KB 堆栈。
- `retriever.stats` 统计降级次数，评测脚本检测到降级即判结果无效。

## TD-12 用量换算、购物清单、按食材推荐都不经过 LLM

- 乘除、单位合并、集合匹配是确定性问题，代码计算可复现、可单测；LLM 心算会出错且每次结果不同。
- 推荐用主料覆盖度排序，再做多样性贪心重排（思路同 MMR）：保证每种手头食材都有菜可做。
- 局限：同义词表手工维护；不同单位（g 和 个）不换算、分开列出。

## TD-13 service 层与 MCP 工具分离

- fastmcp 的 `@mcp.tool` 装饰后函数不能直接调用；业务逻辑放 `CookerService`，MCP 工具只做参数整理和异常翻译（领域异常 → `ToolError`，模型能读懂并修正参数）。
- P2 的 Agent 可直接调用 service（进程内、无序列化开销），也可通过 MCP 调用。
- 工具返回值加类型注解 → FastMCP 生成 output schema，客户端得到结构化结果。

## TD-14 检索消融评测结果（2026-10-16，eval/retrieval_cases.yaml）

| 配置 | 菜谱 Hit@1 / Hit@5 / MRR@5 | 同义词类 Hit@1 | 技巧 Hit@1 | 平均耗时 |
|---|---|---|---|---|
| BM25 | 0.68 / 0.88 / 0.77 | 0.33 | 0.83 | 2ms |
| 向量 | 0.88 / 0.96 / 0.92 | 0.83 | 0.83 | 182ms |
| 混合（RRF） | 0.76 / 0.96 / 0.84 | 0.50 | 0.83 | 159ms |
| 混合 + 重排 | 1.00 / 1.00 / 1.00 | 1.00 | 1.00 | 493ms |

- **混合检索的 Hit@1 低于纯向量**：RRF 只看排名，BM25 在同义词查询上把错误答案排得很高（「番茄炒蛋」→ 韭菜炒蛋），把融合结果拉低。但混合的 Hit@5 与纯向量持平，召回没有损失，重排负责把正确答案排到第一。
- **仍然保留 BM25**：① 向量服务故障时它是唯一可用的检索；② 精确词（菜名、「1000ml 啤酒」）的召回稳定；③ 消耗约 2ms。
- **局限**：31 条、开发者自写、偏简单，1.00 是「这条链路在这些题上有效」，**不是泛化能力的证明**。没有在这份评测集上调参，以免过拟合。改进方向：补充用户真实提问作为独立测试集；考虑在 BM25 前做查询同义词扩展。

## TD-15 Agent 结构：单个 ReAct Agent + 显式 StateGraph

- **决策**：一个模型 + 9 个工具（7 个菜谱工具 + 档案读写），`StateGraph` 手写 agent / tools / verify 三个节点；复用 langgraph 的 `ToolNode`。
- **不选 supervisor 多 Agent**：工具只有 9 个，一个模型能选准；多一层路由 = 多一次 LLM 调用和一个出错点。参考项目 cook-agent 用了多 Agent，对本项目规模过重。
- **不用 `create_agent` 一行生成**：执行循环是核心逻辑，显式写出便于加入 verify 节点、控制强制工具调用，也便于理解与讲解。
- **工具进程内调用 service，不经过 MCP**：同机没有必要多一次序列化和一个子进程；MCP 保留给外部客户端。代价是工具描述有两份（agent/tools.py 与 mcp_server/server.py）。
- `config/models.yaml` 中的 router、planner 角色目前未使用。

## TD-16 安全约束由代码执行，不交给模型

- 过敏原、忌口：检索类工具的 `_filter()` 把档案约束强制并入 `RecipeFilter`，并通过 `auto_applied` 告知模型；`get_recipe` 不拦截但返回 `profile_warning`。
- `ToolRuntime` 注入的 `runtime` 参数不出现在发给模型的工具 schema 中——模型看不到、也伪造不了用户身份与档案。
- 移除过敏原（放宽安全约束）用 `interrupt()` 要求用户本人确认；添加约束不需要确认（方向是更安全）。

## TD-17 回答校验：确定性 verify 节点 + 强制工具调用重答

- **问题**：只靠 Prompt，qwen-plus 36 次场景评测中 10 次失败（没查就答、引用库外菜名）。
- **约定**：库内菜写成【菜名】——既可自动校验，也是 P3 前端渲染菜谱卡片的锚点。
- **verify 规则（纯代码）**：每个【菜名】必须在库中且出现在对话的工具结果里；未加【】但提到的库内菜名同样要有工具依据。
- **重答**：删除不合格回答（RemoveMessage）+ 隐藏的反馈消息（id 前缀 `do-not-render-`，agent-chat-ui 不显示）；重答时 `tool_choice="required"`。最多 2 次，仍不合格则追加「未能与菜谱库核对」提示，如实交付。
- **不选 LLM 当裁判**：多一次调用、本身也会误判；名称存在性用代码就能精确判断。
- **局限**：只校验「引用的菜存不存在」，不校验「对菜的描述是否正确」。实测出现过「微波炉荷包蛋能用上土豆」这类描述错误，verify 无法发现。
- **代价**：流式输出时，用户可能先看到被驳回的草稿（CLI 中可见；前端按 state 渲染，删除后消失）。

## TD-18 记忆分层

| 记忆 | 机制 | 内容 |
|---|---|---|
| 短期 | checkpointer（thread_id） | 本会话消息；裁剪到约 12k token 再发给模型 |
| 长期 | store（namespace = users/user_id） | 结构化档案：过敏、忌口、素食、人数、厨具、厨艺 |

- 档案在每轮 agent 调用时读取并写进系统提示词：修改后下一轮立即生效。
- 不做向量化「语义记忆」：偏好是少量结构化字段，按键读写即可。
- `trim_messages(start_on="human")`：保证裁剪后不会留下没有对应 tool_calls 的 ToolMessage（接口会拒绝）。

## TD-19 Agent 模型选型：qwen-plus → qwen3.7-plus（仍以 Qwen 为默认厂商）

同一套代码与评测（eval/run_agent_eval.py，12 场景 × 3 次），逐步改进与换模型的结果：

| 阶段 | agent 模型 | 通过 | verify 介入 | 平均耗时 |
|---|---|---|---|---|
| 只有 Prompt | qwen-plus | 26/36 | — | 7.8s |
| + verify 节点 | qwen-plus | 30/36 | 9 | 11.0s |
| + 重答强制调工具 | qwen-plus | 34/36 | 9 | 12.1s |
| + 工具描述划清边界 | qwen-plus | 33/36（tip 场景 0/3） | 7 | 10.8s |
| + 按问题类型决定是否强制 | qwen-plus | tip 场景 2/5 | — | ~31s |
| 换模型 | **qwen3.7-plus** | 35/36 → 修正判定后 36/36 | 1 → 0 | 6.1–6.3s |
| 对照 | deepseek-v4.1-flash | 36/36（两次） | 0 | 5.2–5.3s |

- **结论**：工程手段把 qwen-plus 从 72% 提到约 94%，剩余失败集中在「技巧问题里顺手举库外菜例」，属于模型倾向，继续加规则收益很低；
  换成 qwen3.7-plus 后同样的代码几乎不再触发校验。verify 节点保留，作为模型退化或切换时的兜底。
- **注意**：「看到结果后修改判定条件」只发生过一次（multi_turn_followup：指代不明时反问是正确行为），原因与修改都记录在评测脚本中。
- **未验证**：qwen3.7-plus 与 qwen-plus 的价格差异未核实，需在百炼控制台确认。

## TD-20 前端：基于 agent-chat-ui 二次开发，最小改动

- **复用**：agent-chat-ui（MIT，Next.js + `@langchain/langgraph-sdk` 的 `useStream`）已有流式渲染、会话历史、中断、分支切换；直接连 `langgraph dev`（:2024），不写后端聊天接口。
- **改动集中在** `web/src/components/cooker/`（新组件）与 `web/src/lib/cooker.ts`；对原文件只做接线：
  - `markdown-text.tsx`：自定义 `a` 组件，`#recipe:` 链接渲染为菜谱按钮
  - `messages/ai.tsx`：回答先 `linkifyRecipes()`；`type: "confirm"` 中断渲染为确认卡片
  - `messages/tool-calls.tsx`：默认显示中文一行摘要，展开看原始参数；购物清单渲染为可勾选清单
  - `thread/index.tsx`：抽出 `submitMessage`（输入框、示例问题、菜谱抽屉共用）；每次运行传 `context.provider`；中文文案
- **【菜名】→ `#recipe:` 锚点链接**：react-markdown 会清洗非 http(s) 协议的 URL，用锚点最稳；按钮点击不跳转，只打开抽屉。
- **菜谱详情走自定义路由**（`src/cooker/api.py`，挂在 LangGraph Server 的 `http.app`）：前端点击卡片不经过 LLM，零 token、毫秒级。
  路由用同步 `def`（Starlette 放进线程池），避免阻塞 IO 卡住同进程的流式对话。
- **图片接口的路径穿越防护**：`resolve()` 后用 `is_relative_to(dishes 根)` 判断，越界 / 非图片 / 不存在统一 404；测试覆盖 `..`、URL 编码、绝对路径。
- **模型切换**存在 URL 查询参数 `?provider=`，经 `context` 传给后端 `Context.provider`（实测 HTTP 传入的 dict 能正确转换为 dataclass）。

## TD-21 麦当劳技能：按需加载（渐进式披露），单图模式（2026-10-31）

- **背景**：旧版接入（归档在分支 `archive/mcd-v1`）把 SKILL.md 复制进仓库却没有代码读取它；13 条手写规则每轮都注入，稀释做饭提示词；
  写操作在独立的表单工作台页；用正则关键词判断要不要查麦当劳。
- **做法**：`skills/*/SKILL.md` 原样存放；系统提示词只放 name + description（约 300 字）。模型判断命中后调用 `load_skill`，
  状态 `skills` 记录已加载技能 → 之后每次调用模型时系统提示词附上 SKILL.md 正文 + 宿主适配说明，并绑定 36 个麦当劳工具；
  references 由 `read_skill_file` 按需读取（白名单，只能读 references/*.md）。
- **为什么正文放系统提示词**：历史按 12k token 裁剪，正文若作为 ToolMessage 会被很快裁掉；放系统提示词整段会话稳定生效。
- **为什么不用多 Agent / 路由模型**：同一个 ReAct 循环就能完成路由（模型自己决定是否 load_skill），不增加 LLM 调用；做饭对话零额外工具描述。
- **技能正文不改**：与宿主的差异（工具改名、无工作台、确认卡由系统生成、【】只给菜名）写在 `src/cooker/mcd/host_adapter.md`，附在正文后，优先级更高（瑞幸同理：`src/cooker/luckin/host_adapter.md`）。
- **工具定义来自快照**：`scripts/sync_mcp_tools.py <mcd|luckin>`（原 sync_mcd_tools.py）只调 `list_tools` 拉官方结构写入 `tool_schemas.json`；绑定时离线、确定，调用前再用线上结构校验一次。
  首次同步就发现文档写的 33 个工具与服务端 36 个不一致（`mall-points-products` 已改名 `query-mall-products`，派对工具名不同，新增 3 个），
  硬编码工具名会直接失败。新工具必须手动归入读 / 写白名单才会暴露给模型。
- **代价（实测字符数）**：做饭提示词 1235 字（含技能索引）；加载后系统提示词约 16.4k 字 + 工具结构约 35k 字，每次模型调用都要付这部分输入 token。
  可优化方向：按能力模块分组绑定工具（见 P5 方案）。
- **局限**：技能加载后整段会话保持激活，用户转回做饭话题时仍带着麦当劳正文与工具。

## TD-22 麦当劳写操作：确认卡 + 指纹 + 台账（2026-10-31）

- **7 个写工具**（下单、取消、商城兑换、主题活动下单、抽奖、领券、新增地址）统一走 `run_write`（接入瑞幸时移到 `cooker/connectors/actions.py`，见 TD-24）：
  1. 台账查 tool_call_id：已执行过 → 返回记录，不重发（崩溃重放、重复恢复的幂等）；
  2. 账户有 IN_FLIGHT / UNKNOWN → 拒绝（FROZEN），不弹卡；
  3. 用快照结构校验参数，**用实时只读查询生成确认卡**：下单前重新 `calculate-price`（分→元由代码换算）并校验 takeWayCode；
     抽奖前重新读 `drawDecision`，`resourceEligible` 不为 true 直接拒绝；取消前查订单状态；
  4. `interrupt({"type":"action_confirm", connector, rows, fingerprint})`（原 `mcd_confirm`），前端渲染确认卡；
  5. 恢复时工具从头重跑、重新建卡；用户带回的指纹与新卡指纹不一致 → CHANGED，不执行；
  6. `BEGIN IMMEDIATE` 事务内认领（检查冻结 + 抽奖 1 秒频控 + 插入 IN_FLIGHT）→ 发请求 → SUCCESS / FAILED / UNKNOWN。
- **UNKNOWN 的判定**：请求已发出后超时或断连。不能证明没执行，冻结全部写操作；解冻只能本人核实后跑
  `python -m cooker.connectors.ledger resolve <id> FAILED|SUCCESS --note ...`，不做成工具，模型无法自行解冻。
- **为什么卡片不让模型写**：模型说错的金额会被用户「确认」进去；卡片数据来自代码的实时查询，模型只决定调用哪个工具和参数。
- **保留旧版的正确决策**：读写白名单、线上结构校验、结果中的 Token 替换、只允许官方端点。旧版 `data/mcd-actions.sqlite3`（表结构不同，0 条执行记录）不再使用，新台账为 `data/connector-ledger.sqlite3`（最初叫 mcd-ledger.sqlite3，接入瑞幸后改名，记录原样保留）。
- **局限**：台账只防护经过 COOKer 的调用，挡不住手机 App 或其他客户端；商城兑换卡片拿不到商品价格（接口需要 spuId，而下单参数只有 skuId），只展示 SKU、数量和当前积分并提示「以下单返回为准」。

## TD-23 聊天中的菜品图片：前端按【菜名】取图，不让模型输出图片（2026-10-31）

- **决策**：回答结束后，前端提取【菜名】调用 `GET /cooker/covers` 批量取封面，渲染在回答下方；模型不输出任何图片地址。
- **备选**：① 模型输出 `![](url)`：可能抄错或编造地址、多花 token、数量不可控；② 工具结果下方直接画图：工具查到的菜不等于最终推荐的菜。
- **封面选择**：HowToCook 多图菜谱常按步骤排列，按文件名打分（成品类字样 > 与菜名一致 > 摆盘 > 原文顺序）；详情接口同样把封面排第一。启发式，不保证全对。
- **请求控制**：流式输出期间不请求；页面级缓存（含「无图」）+ 请求中去重（single-flight），实测一条回答 1 次请求。
- **代价**：未生成缩略图，卡片加载原图（中位数约 211 KB）。

## TD-24 外部服务抽象成连接器：接入瑞幸时把麦当劳的流程提为通用层（2026-10-31）

- **背景**：瑞幸（my-coffee 技能）需要的机制与麦当劳完全相同——按需加载技能、白名单 + 结构校验、写操作确认卡、台账。复制一份 mcd 代码会让两处安全逻辑各自演化。
- **决策**：通用层 `cooker/connectors/`（base / client / actions / ledger / tools）只写一次；每个服务一个包，只提供 `Connector`：
  官方地址、凭据前缀与文件、读写白名单、**每个写工具一个确认卡函数**（启动时断言全覆盖）、返回值处理 `present`。`all_connectors()` 登记全部服务并断言工具不重名。
- **卡片可以改写最终参数**（`Card.args`）：瑞幸要求 `previewOrder` 的 `couponCodeList` 原样透传、下单用门店坐标——由代码从预览结果写入，
  模型漏传或传错都会被覆盖；参数进入指纹，用户确认的就是实际执行的。
- **台账按「服务 + Token 摘要」隔离**：瑞幸一笔结果未知只冻结瑞幸，不影响麦当劳。
- **验证**：重构后麦当劳原有 17 个测试不改断言全部通过；新增瑞幸 10 个测试。
- **技能与宿主冲突的处理**：my-coffee 默认宿主有 shell（curl 调接口、ipinfo.io 按 IP 定位、Token 写入 `~/.my-coffee/`）。
  COOKer 没有命令行，Token 由宿主配置文件管理，这些规则在 `luckin/host_adapter.md` 中明确覆盖；
  技能许可证 CC BY-ND 4.0 不允许改动正文，正好与「正文原样、差异写适配说明」的做法一致。
- **局限**：订单详情字段名尚未用真实订单核对（取消卡按常见字段名尝试，取不到显示「数据缺失」）；位置只能靠用户描述或模型对地名的粗略坐标，未接浏览器定位。

## TD-25 外部服务商品图：工具 artifact 给界面，content 给模型（2026-11-01）

- **决策**：连接器工具使用 `response_format="content_and_artifact"`。后端从返回中提取 `[{name, url}]` 放进 `ToolMessage.artifact`（保存在会话、前端可读、不发给模型）；
  给模型的 content 删除图片字段（`strip_images`，不影响 `payOrderQrCodeUrl`）。前端只显示回答中提到的商品图。
- **为什么**：与 TD-23 同一原则——确定性数据由代码传递，不让模型抄写 URL；同时省 token（麦当劳菜单 -34% 字符）。
- **安全**：图片必须 https 且域名属于连接器声明的官方图床（`image_hosts`，含子域名、防后缀伪装），避免外部返回内容诱导浏览器请求任意地址；前端 `referrerPolicy="no-referrer"`。
- **踩坑**：「一轮」的边界要跳过 verify 插入的隐藏用户消息，否则重答后图片丢失。
- **局限**：名称按字符串包含匹配；不生成缩略图。展示位置后改为就地展示，见 TD-26。

## TD-26 图片就地展示：前端 rehype 插件按名称定位（2026-11-01）

- **决策**：不再在回答下方集中显示图片。react-markdown 渲染时用 rehype 插件改写 HTML 语法树：找到列表项 / 表格单元格 / 标题 / 段落里第一次出现的【菜名】链接或商品名，
  列表项改为左图右文，其余在名称前插小图；每张图每条回答一次。
- **备选**：模型输出 `![](url)`（地址交给模型）；模型输出占位标记（多一种格式要遵守，漏写就没图）。都不如按已有的名称确定性定位。
- **规则**：列表项只有「第一个出现的名称」有图时才用卡片布局（左侧 128px），否则会让图片看起来属于前一个名称；块内其余有图名称放在项末尾 / 段落下方的图片行（144px，带名称）；表格 64px。同一列表有一项配图则整列统一卡片样式。不在文字行内塞小图——默认状态下就要看得清，点击放大只是附加。
- **同日按官方文档完善连接器**：HTTP 401 / 403 / 429 明确提示（httpx 响应钩子记录状态码，仅限写请求未发出时翻译）；瑞幸订单状态码、`orderIdStr`（19 位数字超出 JS 安全整数）、
  `needPay`；连接器新增 `local_tools` 扩展点，瑞幸用它从台账提供「最近订单」（官方没有订单列表接口）。
