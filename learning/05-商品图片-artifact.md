# 麦当劳 / 瑞幸商品图：一步一步怎么做的

> **2026-11-01 更新**：展示位置已改为就地展示（`learning/06`）；`product-images.tsx` 与 `mentionedImages` 已删除，名称匹配移入 rehype 插件。artifact、白名单、去掉给模型的图片地址等后端设计不变。

> 2026-11-01。设计结论：`docs/technical-decisions.md` TD-25；开发记录：`docs/development-2026-11-01.md`。
> 前置阅读：`learning/03-聊天中显示菜品图片.md`（菜谱图「代码取图、模型不输出图片」的思路）、`learning/04-瑞幸技能接入-逐步实现.md`（连接器）。
> 标注：**已验证** = 本次实际跑过。

## 0. 需求

工具返回里有商品图（比如瑞幸 `pictureUrl`），顺便显示出来。

## 第 1 步：先摸清有哪些图片字段（只读）

| 服务 | 字段 | 出现在 | 图床域名 |
|---|---|---|---|
| 瑞幸 | `pictureUrl` | 商品搜索、商品详情 | `img06.luckincoffeecdn.com` |
| 瑞幸 | `breviaryPicUrl` / `bigPicUrl` | 订单预览的商品 | 同上 |
| 麦当劳 | `image` | 菜单 `query-meals` 的每个餐品 | `menu-img.mcd.cn` |
| 麦当劳 | `imageUrl` | 抽奖奖品、中奖记录 | `img.mcd.cn` |
| 麦当劳 | `couponImage` | 领券结果 | `img.mcd.cn` |

规律：**图片字段旁边总有名称字段**（`productName` / `name` / `couponName`）。名称就是把图片和回答对应起来的钥匙。

## 第 2 步：关键决策——图片地址走哪条路到界面？

延续菜谱图的原则：图片地址是确定性数据，不交给模型抄写。但这次图片在**工具结果**里，于是用 LangChain 工具的
**content / artifact 分离**：

```python
StructuredTool.from_function(..., response_format="content_and_artifact")
# 工具函数返回 (content, artifact)
#   content  → ToolMessage.content，发给模型
#   artifact → ToolMessage.artifact，随会话保存、前端能读到，但不会发给模型
```

所以：

- **artifact**：后端从返回里提取 `[{name, url}]`，界面据此显示图片；
- **content**：给模型的文本里把图片地址**删掉**——模型不需要它们，删掉既省 token，也防止模型自己在回答里贴 `![](...)`。

实测效果（已验证）：麦当劳一份菜单交给模型的文本从 **14475 → 9502 字符（-34%）**。

**Java 类比**：像接口返回里分 `body`（给调用方的业务数据）和 `headers/metadata`（给基础设施用的数据）。模型是「调用方」，界面是「基础设施」，各取所需。

## 第 3 步：后端 `src/cooker/connectors/images.py`

- `extract_images(payload, hosts)`：递归遍历返回结构，找「同一个对象里既有名称字段、又有图片字段」的条目，按地址去重，最多 80 张。
  上限一开始设 30，实测麦当劳整份菜单超过 30 个餐品，回答里提到的可能排在后面，改成 80（artifact 不发给模型，多存一些成本很低）。
- **域名白名单**：每个连接器声明 `image_hosts`（麦当劳 `mcd.cn`、瑞幸 `luckincoffeecdn.com`，含子域名），图片必须是 https 且域名命中。
  为什么要白名单：工具返回来自外部服务，若其中混入任意地址，浏览器加载时就会向那个地址发请求（追踪像素就是这么工作的）。
  测试覆盖了 http、陌生域名、`luckincoffeecdn.com.evil.example` 这种后缀伪装。
- `strip_images(payload)`：给模型的文本里删掉这些图片字段。**只删图片字段名单里的键**，支付二维码 `payOrderQrCodeUrl` 不受影响（瑞幸技能要求模型展示它）。
- 接线：`connectors/tools.py` 读工具返回 `(for_model(...), {"images": ...})`；`client.for_model` 先 `strip_images`。

瑞幸下单确认卡也加了商品缩略图：`Card.images` 从预览结果提取，随 interrupt 发给前端（**不参与指纹**——图片换了不影响用户确认的内容）。

## 第 4 步：前端

`web/src/lib/cooker.ts`：

- `turnImages(messages, messageId)`：从这条回答往前找，直到上一条**真实**用户消息，收集所有工具消息 `artifact.images`。
- `mentionedImages(text, images)`：只保留回答里提到的商品。名称后缀括号常被省略（「生椰拿铁（原创）」写成「生椰拿铁」），两种写法都算。
  为什么要过滤：一次搜索可能返回很多商品，模型只推荐其中一两个，全部显示会很乱。

`components/cooker/product-images.tsx`：回答下方的图片条，流式输出结束后才显示；`referrerPolicy="no-referrer"`，加载外部图床时不带本机页面地址。
确认卡 `action-confirm.tsx` 顶部显示缩略图。

## 第 5 步：真实环境发现的 bug——verify 重答后图片丢失

麦当劳场景（问抽奖奖品）在浏览器里第一次没出图，第二次又正常。排查过程：

1. 在进程内直接跑同一句话：8 张奖品图全部命中 → 后端提取和名称匹配都没问题。
2. 怀疑点：verify 节点不通过时会插入一条**隐藏的用户消息**（id 以 `do-not-render-verify-` 开头）让模型重答；
   而 `turnImages` 往回找时**遇到用户消息就停**——重答发生时，隐藏消息之前的工具结果全被跳过。
3. 第一次失败的现场没保存，无法证明那次就是这个原因；但这个逻辑缺陷本身可以确定地复现：
   在预置会话里插入一条隐藏校验消息，**只回退那一行判断**就失败，恢复修复就通过（已验证，干净 A/B）。

修复：遇到 id 以 `do-not-render-` 开头的用户消息不停，继续往前找。回归脚本的预置会话从此固定包含这种消息。

**教训**：

- 「偶发失败」不要靠重跑到通过就算了。先找能确定复现的路径，再修。
- 做对照实验时要只改一个变量。第一次我用 `git stash` 回退了整个文件，连 `turnImages` 都没了，那个「修复前失败」不能证明任何事，后来改成只回退一行。
- 往回追溯「一轮」时，要知道系统里还有哪些不显示给用户、但存在于消息列表里的消息（这里是 verify 的校验反馈）。

## 第 6 步：验证

| 项目 | 结果 |
|---|---|
| `tests/test_images.py`（3 个）：白名单、名称配对与去重、删图片保留支付二维码 | 通过 |
| `tests/test_luckin.py` 新增 2 个：图片进 artifact 不进模型输入（`convert_to_openai_messages` 后检查）、下单卡带缩略图 | 通过；全量 97 通过 |
| 浏览器 `verify-product-images.mjs`（预置会话，含隐藏校验消息） | 只显示被提到的 1 件商品，图片真实加载 |
| `--real`（真实模型 + 瑞幸） | 「来福士店有生椰拿铁吗」→ 回答下方显示生椰拿铁商品图 |
| `--real-mcd`（真实模型 + 麦当劳抽奖奖品） | 第 1 次未出图（原因未查实），修复后再跑显示 6 张奖品图 |
| 瑞幸确认卡 `verify-action-confirm.mjs luckin` | 卡片顶部显示商品缩略图。3 次运行 2 次通过，第 1 次失败的报错未留存（依赖真实模型，原因未查实） |

## 局限

- 名称匹配是字符串包含：模型把多个商品合写成「下单立减 3/2/1 元券」时会匹配不上，图片就不显示（宁可不显示，也不显示错）。
- 图片直接加载官方原图，没有缩略图。

## 自测题

1. content 和 artifact 分别给谁用？为什么商品图地址放 artifact 而不是 content？
2. 删掉 content 里的图片地址，会不会影响瑞幸展示支付二维码？为什么？
3. 图片域名白名单防的是什么？`endswith("luckincoffeecdn.com")` 为什么不够，要写成 `endswith(".luckincoffeecdn.com")`？
4. verify 重答为什么会让图片消失？你会怎么写一个确定能复现它的测试？
