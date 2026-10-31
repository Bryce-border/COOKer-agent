# 开发记录 2026-11-01：麦当劳 / 瑞幸商品图

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
