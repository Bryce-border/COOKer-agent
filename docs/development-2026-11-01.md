# 开发记录 2026-11-01：麦当劳 / 瑞幸商品图；图片就地展示；按官方文档完善连接器

逐步讲解见 `learning/05-商品图片-artifact.md`；设计结论见 [技术决策](technical-decisions.md) TD-25。

## 解决的问题

外部服务工具返回里的商品图（瑞幸 `pictureUrl`、麦当劳菜单 `image`、奖品 `imageUrl` 等）现在会显示：
回答下方显示回答中提到的商品图；瑞幸下单确认卡顶部显示商品缩略图。

## 改动清单

| 位置 | 内容 |
|---|---|
| `src/cooker/connectors/images.py` | 新增：`extract_images`（名称 + 图片配对、https + 官方图床白名单、去重、最多 80 张）、`strip_images`（给模型的文本删掉图片字段） |
| `src/cooker/connectors/tools.py` | 工具改为 `response_format="content_and_artifact"`：content 给模型，artifact `{images}` 给界面 |
| `src/cooker/connectors/client.py` | `for_model` 先去掉图片地址 |
| `src/cooker/connectors/base.py`、`actions.py` | `Connector.image_hosts`；`Card.images` 随 interrupt 发出（不参与指纹） |
| `src/cooker/mcd/connector.py`、`luckin/connector.py` | 声明图床 `mcd.cn` / `luckincoffeecdn.com`；瑞幸下单卡带缩略图 |
| `web/src/lib/cooker.ts` | `turnImages`（本轮工具结果的图片，跳过 verify 隐藏消息）、`mentionedImages`（只留回答提到的商品） |
| `web/src/components/cooker/product-images.tsx` | 回答下方的商品图片条（`referrerPolicy="no-referrer"`） |
| `web/src/components/cooker/action-confirm.tsx` | 确认卡缩略图 |
| `tests/test_images.py`、`tests/test_luckin.py` | 新增 5 个测试 |
| `web/scripts/verify-product-images.mjs` | 浏览器回归：预置会话（默认）/ `--real` 瑞幸 / `--real-mcd` 麦当劳；失败时打印会话消息结构 |

## 遇到的问题

1. **给模型的文本仍带图片地址**：artifact 只是另存一份，不会从 content 删除 → 增加 `strip_images`。麦当劳菜单给模型的文本 14475 → 9502 字符（-34%）。
2. **图片上限 30 不够**：麦当劳整份菜单超过 30 个餐品 → 提到 80。
3. **verify 重答后商品图消失**：`turnImages` 遇到 verify 插入的隐藏用户消息就停止往回找 → 跳过 `do-not-render-` 消息。
   预置会话插入隐藏消息，只回退这一行就失败、恢复就通过（干净 A/B）。真实麦当劳场景第一次未出图的确切原因未查实。

## 验证

| 项目 | 结果 |
|---|---|
| pytest | 97 通过（新增 5） |
| 前端 tsc、eslint | 通过 |
| 浏览器预置会话 | 只显示被提到的商品，图片真实加载（含隐藏校验消息的场景） |
| 真实模型 + 瑞幸 | 回答下方显示「生椰拿铁（原创）」商品图；确认卡顶部显示缩略图 |
| 真实模型 + 麦当劳 | 奖品图 6 张（修复后）；修复前一次未出图 |
| 确认卡回归 `verify-action-confirm.mjs luckin` | 3 次 2 次通过，第 1 次失败报错未留存，原因未查实 |

## 局限

- 名称按字符串包含匹配，模型合写商品名（如「下单立减 3/2/1 元券」）时不显示图片。
- 直接加载官方原图，没有缩略图。

---

# 图片就地展示；按官方文档完善连接器

逐步讲解见 `learning/06-图片就地展示与按官方文档完善.md`；设计结论见 [技术决策](technical-decisions.md) TD-26。

## 解决的问题

1. 图片不再集中显示在回答下方，而是贴在对应信息旁：列表项左图右文，表格 / 段落 / 标题在名称前插小图。菜谱封面与商品图都适用。
2. 读麦当劳、瑞幸官方接入文档，补上能用的信息：订单状态码、19 位订单号、无需支付、最近订单、401 / 429 提示。

## 改动清单

| 位置 | 内容 |
|---|---|
| `web/src/lib/inline-images.ts` | 新增 rehype 插件：按【菜名】链接与商品名定位，每张图每条回答一次；列表项第一个名称有图才用卡片布局 |
| `web/src/components/thread/markdown-text.tsx` | `img` 组件（就地配图 / Markdown 原图如支付二维码）；接入插件 |
| `web/src/components/thread/markdown-styles.css` | `.cooker-media`、`.cooker-media-list` 卡片样式 |
| `web/src/components/cooker/recipe-covers.tsx` | 图片条组件改为 `useRecipeCovers` hook；删除 `product-images.tsx` |
| `web/src/components/thread/messages/ai.tsx` | 组装配图数据传给 MarkdownText |
| `src/cooker/connectors/client.py` | httpx 响应钩子记录状态码；401 / 403 / 429 明确提示（仅限写请求未发出时） |
| `src/cooker/connectors/base.py`、`tools.py`、`__init__.py`、`ledger.py` | `local_tools` 扩展点、`skill_guard`、`Ledger.history` |
| `src/cooker/luckin/connector.py` | 取消卡按文档字段（状态码、`orderPayAmount`、`orderCommodityList`），80 / 100 拒绝；`orderId` 用 `orderIdStr`；本地工具 `luckin_recent_orders` |
| `src/cooker/mcd/connector.py` | 订单已完成 / 已取消不弹取消卡 |
| 两份 `host_adapter.md` | 商品图由界面自动显示、逐个写全名；瑞幸 `needPay`、`operation=3`、状态码、最近订单 |
| `tests/test_connector_client.py`（新）、`tests/test_luckin.py` | 新增 5 个测试 |
| `web/scripts/verify-recipe-covers.mjs`、`verify-product-images.mjs` | 断言改为「图片在对应列表项 / 单元格 / 段落里」 |

## 遇到的问题

1. **传输层工厂签名不兼容，所有真实调用失败**：fastmcp 调用工厂时多传 `follow_redirects`，旧签名直接 TypeError。离线测试用假传输没发现，真实探测时发现钩子没记到任何状态码，读源码定位。已修复并加测试。
2. **MCP SDK 吞掉 HTTP 状态码**：401 只表现为 JSON-RPC -32603 → 用 httpx 响应钩子自己记录。
3. **本地工具最初写成 async、且直接打开默认台账**：同步调用不可用、测试读到真实台账 → 改为同步函数、与 `run_write` 共用 `actions.Ledger`。
4. **真实回答的两个样式问题**：图片挂到列表项最左边但对应的是第二个名称；同一列表卡片与圆点混排 → 「第一个名称」规则 + `.cooker-media-list`。

## 验证

| 项目 | 结果 |
|---|---|
| pytest | 102 通过 |
| 前端 tsc、eslint | 通过（5 条既有 react-refresh 警告，与改动前相同） |
| 浏览器预置会话（菜谱 / 商品） | 图片在对应列表项、单元格、段落；规则与去重符合预期 |
| 真实模型：瑞幸、麦当劳奖品表格、菜谱推荐 | 图片在对应位置，真实加载 |
| 错误 Token 真实探测 | 两个服务都提示「密钥无效或已过期」；真实 Token 调用正常 |

**未验证**：瑞幸订单详情 / 最近订单的真实数据（仍未真实下单，字段来自官方文档示例）；429 只用模拟测试。

## 方案（未实施）

- 麦当劳取餐柜二维码（`lockerQrCode`）展示：需真实订单确认字段与图床。

## 追加：图片放大；麦当劳确认卡回归场景更新

- **图片太小看不清（用户反馈）**：列表卡片图 64px → 96px，表格 / 段落小图 28px → 56px，确认卡缩略图 64px → 96px；商品图点击放大（`components/cooker/image-lightbox.tsx`，Esc 或点任意处关闭）；菜谱图点击仍打开菜谱抽屉。
  `verify-product-images.mjs` 增加「行内图 ≥ 48px」与放大 / 关闭断言。
- **`verify-action-confirm.mjs mcd` 失败**：诊断输出显示模型先查了可领券，3 张都在 10-31 真实领过，于是如实回答不必再领、没有调用写工具——是测试场景过时，不是代码问题。
  场景改为「新增收货地址」（卡片不依赖账户状态），只点取消；脚本失败时打印会话消息与中断，便于区分「模型没调写工具」与「卡片没渲染」。
- 验证：三个浏览器回归（菜谱配图、商品图、麦当劳确认卡）通过；前端 tsc 通过。
