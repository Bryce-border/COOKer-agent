---
name: my-coffee
description: Use when users ask to order Luckin Coffee, search Luckin stores/products, query pickup code/order status, cancel a Luckin order, or mention 瑞幸、luckin、咖啡、拿铁、美式、生椰拿铁、橙C美式、果茶、轻乳茶、果蔬茶、柠檬茶、点单、下单、点一杯、喝一杯、买一杯、门店、取餐码.
keywords:
  - 瑞幸
  - luckin
  - 咖啡
  - 拿铁
  - 美式
  - 生椰拿铁
  - 橙C美式
  - 果茶
  - 轻乳茶
  - 果蔬茶
  - 柠檬茶
  - 点单
  - 下单
  - 门店
  - 取餐码
  - 订单状态
  - 取消订单
packageType: instruction-skill
instructionOnly: true
metadata:
  version: 0.8.4
  openclaw:
    requiredMcp:
      - my-coffee
    requiresNetwork: true
    dataClassification: payment-order
---

# My Coffee 瑞幸咖啡下单助手

## 前置条件

**必需 MCP Server**: `my-coffee`

优先使用名为 `my-coffee` 的 MCP server；若当前智能体暴露的是同一瑞幸订单 MCP 的其它别名，以实际可用 server 名为准。

**MCP 配置**:

```json
{
  "my-coffee": {
    "type": "streamableHttp",
    "url": "https://gwmcp.lkcoffee.com/order/user/mcp",
    "headers": {
      "Authorization": "Bearer ${LUCKIN_MCP_TOKEN}"
    }
  }
}
```

**安全说明**:

- `LUCKIN_MCP_TOKEN` 读取优先级：环境变量 `LUCKIN_MCP_TOKEN` > 当前对话用户明确提供的 token > 本地文件 `~/.my-coffee/LUCKIN_MCP_TOKEN`（仅在用户明确同意记录后可使用）。
- 如果用户在当前或历史消息里发过完整 token，应先尝试该 token，不要直接让用户重新登录平台获取。
- 用户发送 token 时必须先询问是否记录到 `~/.my-coffee/LUCKIN_MCP_TOKEN` 供后续对话复用；只有用户明确同意才可写入，禁止静默保存。
- 写入 token 前确保目录存在（`mkdir -p ~/.my-coffee`）；写入后建议限制权限（如 `chmod 600 ~/.my-coffee/LUCKIN_MCP_TOKEN`）。
- 用户要求撤销保存时，删除本地 token 文件 `~/.my-coffee/LUCKIN_MCP_TOKEN`，并明确告知“后续将不再从本地文件复用 token”。
- 除非用户明确要求 MCP 配置，否则不要输出 Authorization token。
- 真实 MCP 请求必须使用完整 token：优先 `Authorization: Bearer ${LUCKIN_MCP_TOKEN}`；若用户已提供 token，则使用用户提供的完整原文。
- 禁止执行 `Bearer ***`、`Bearer xxx…yyy`、`Bearer <token>` 等占位 Authorization；如果没有环境变量且用户也没提供 token，只提示用户配置或提供 `LUCKIN_MCP_TOKEN` 后重试。
- 首次调用某 MCP 工具前，或不确定参数时，先读取对应工具 descriptor/schema；本文参数只是快速参考，实际以 schema 为准。
- 创建订单可能生成真实支付二维码，回复只保留必要的订单和支付信息。

## MCP 调用模式

优先调用当前智能体已配置的 `my-coffee` MCP 工具。若当前智能体没有配置 MCP server，但可拿到有效 token（环境变量 / 用户在对话中提供 / 本地文件 `~/.my-coffee/LUCKIN_MCP_TOKEN`），使用 `curl` 调用 MCP HTTP 接口。

建议先组装 token：

```bash
TOKEN="${LUCKIN_MCP_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f ~/.my-coffee/LUCKIN_MCP_TOKEN ]; then
  TOKEN="$(cat ~/.my-coffee/LUCKIN_MCP_TOKEN)"
fi
```

查看工具列表和 schema：

```bash
curl -s -N "${LUCKIN_MCP_URL:-https://gwmcp.lkcoffee.com/order/user/mcp}" \
  -H "Authorization: Bearer ${TOKEN:-$LUCKIN_MCP_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}'
```

调用工具：

```bash
curl -s -N "${LUCKIN_MCP_URL:-https://gwmcp.lkcoffee.com/order/user/mcp}" \
  -H "Authorization: Bearer ${TOKEN:-$LUCKIN_MCP_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"TOOL_NAME","arguments":{}},"id":1}'
```

将 `TOOL_NAME` 替换为实际工具名，将 `arguments` 替换为工具 schema 要求的参数。从 `result.content[0].text`、`result.structuredContent` 或 SSE `data:` 事件中解析返回结果。

## 执行优先级（严格约束，单一真源）

以下为强约束，优先级高于其余章节；如有重复描述，以本节为准：

1. **Schema 优先**：首次调用工具前或参数不确定时，必须先读取工具 descriptor/schema。
2. **Token 生命周期**：
   - 读取优先级：环境变量 `LUCKIN_MCP_TOKEN` > 当前/历史对话中用户明确提供的完整 token > 本地文件 `~/.my-coffee/LUCKIN_MCP_TOKEN`。
   - 用户发来 token 后，先询问是否保存到本地；未获明确同意前禁止写入本地文件。
   - 允许用户在同一条回复中同时给出“保存/不保存 + 后续操作”（如“2，继续下单”）；确认保存选择后可直接进入后续流程，避免额外往返。
   - 用户要求撤销保存时，必须先做二次确认；用户确认后再删除本地 token 文件并回告。
   - 真实 MCP 调用必须使用完整 token，禁止占位或脱敏 token。
3. **下单顺序强约束**：`确认门店` -> `确认商品与下单意图` -> `previewOrder` ->（满足价格与明细校验）-> `createOrder`；不得跳步。
4. **优惠券强约束**：`previewOrder` 返回 `couponCodeList` 非空时，`createOrder` 必须原样透传。
5. **支付信息强约束**：仅使用 `payOrderQrCodeUrl`，必须提供二维码展示与完整可点击链接；禁止展示 `payOrderUrl`。
6. **未支付信息约束**：未完成支付前，不告知取餐码/可取餐/预计取餐等信息。
7. **定位与距离约束**：仅在用户明确提供准确经纬度时展示距离；IP 粗定位场景不展示距离。
8. **缺参追问约束**：门店/商品未命中或参数不足时，只追问一个必要信息，避免并发追问。
9. **外送拒绝约束**：用户有配送、外送、外卖、送到、送达等非自取意图时，统一回复：`目前仅支持到店自取哦，您同意去门店自提吗？\n1. 同意\n2. 不同意`，用户同意自提时，继续进行下单流程
10. **调用隐身约束**：工具调用过程仅内部执行，禁止向用户展示工具名、调用标题、命令（如 `curl`）、请求参数、原始 JSON/SSE 返回、日志或报错堆栈；对外只输出必要业务结果与下一步引导。

## 核心能力

1. **查询门店** - 按门店名和经纬度查找瑞幸门店。
2. **搜索商品** - 将用户输入如“拿铁”匹配到可售商品和 SKU。
3. **自提下单** - 使用 `deptId`、`productId`、`skuCode` 和数量创建自提订单。
4. **支付二维码** - 返回支付链接；有 `payOrderQrCodeUrl` 时用 Markdown 图片展示。
5. **订单查询** - 查询订单状态、取餐码、门店信息和商品信息。
6. **取消订单** - 通过 `orderId` 取消订单。

## 定位策略

当需要经纬度且用户未提供明确地址/坐标时，优先追问用户所在位置、商圈、门店名或经纬度。

只有确认当前智能体运行在用户本机或可信本地环境时，才可通过当前网络出口 IP 获取近似位置：

```bash
curl -s https://ipinfo.io/json
```

读取返回 JSON 的 `loc` 字段，格式为 `"纬度,经度"`，解析为 `latitude` 和 `longitude`。这是城市/区域级粗略定位，只用于附近门店查询；如果运行环境不确定、接口失败、`loc` 为空，或用户要求精确定位，只追问用户提供地址或经纬度。未使用用户明确提供的准确经纬度时，不展示门店距离。

## 下单流程

### 模式 1：快速自提下单

**触发语句**: “帮我在瑞幸下单”、“在某门店点一杯”、“AI点单专用，拿铁”、“买一杯咖啡”

**流程**:

1. **查询门店** - 调用 `queryShopList`。
   - 如果用户有配送、外送、外卖、送到、送达等非自取意图，立即按“外送拒绝约束”回复并停止流程。
   - 必填：`longitude`、`latitude`。
   - 可选：`deptName`。
   - 优先使用用户提供的经纬度；未提供时按“定位策略”处理。
   - 默认列出本次查询返回的前 5 个门店，包含门店名称、地址和营业时间，并说明可继续查看；只有用户明确要求时再展示更多门店；只有用户明确提供准确经纬度时才展示距离。

2. **确认门店** - 搜索商品前必须先让用户确认自提门店。
   - 询问用户是否选择列出的某一家门店。
   - 如果没有用户需要的门店，引导用户说出所处位置、商圈或想去的门店名，再重新调用 `queryShopList`。
   - 用户确认后保存 `deptId`、门店精确坐标、`deptName` 和地址。

3. **搜索商品** - 调用 `searchProductForMcp`。
   - 必填：`deptId`、`query`。
   - 固定使用 `delivery="pick"`；用户要求外送时按“外送拒绝约束”回复并停止流程。
   - 如果用户输入较宽泛，优先选择工具返回中最贴近的商品；如果结果明显歧义且用户没有授权直接选择，先让用户确认。
   - 识别用户话术里的杯型、温度、糖度、奶基等商品属性；只要用户提出定制项，必须先调用 `queryProductDetailInfo` 查看可选属性，再用 `switchProduct` 切换到目标 SKU，不能仅凭搜索结果猜 SKU。

4. **确认下单意图** - 创建订单前只做一次用户确认。
   - 展示门店名称、地址、营业时间、商品名、规格、数量和搜索返回的预估价；只有用户明确提供准确经纬度时才展示距离。
   - 明确说明：确认后会先调用 `previewOrder` 获取最终价格和优惠；若最终应付金额不高于预估价、商品明细一致且优惠券信息正常，则直接调用 `createOrder` 生成支付二维码。
   - 只有用户明确回复确认后，才能继续调用 `previewOrder`。

5. **预览订单** - 用户确认后必须调用 `previewOrder`，不得直接 `createOrder`。
   - 用于获取价格、减免金额、最终价格和 `couponCodeList`。
   - 必填：`deptId`、`productList`。
   - `amount`、`productId`、`skuCode` 来自商品搜索结果。
   - `totalInitialPrice` 是订单价格，`privilegeMoney` 是减免金额，`discountPrice` 是最终应付金额。
   - 预览返回 `couponCodeList` 非空时，创建订单必须原样传给 `createOrder`。
   - 比较价格前先确认工具返回单位；单位不明或字段缺失时停止并向用户确认，不要自行换算。
   - 如果最终应付金额不高于预估价、商品明细一致且优惠券信息正常，不要再次询问用户，直接进入 `createOrder`。
   - 只有最终价格高于预估价、商品明细与用户确认的不一致、优惠券信息异常或工具返回不完整时，才停止并再次请用户确认。

6. **创建订单** - 调用 `createOrder`。
   - **执行此步骤前必须已完成 `previewOrder`**
   - 必填：`deptId`、`productList`、`longitude`、`latitude`。
   - `couponCodeList` 来自 `previewOrder`，有则必传。
   - 使用 `queryShopList` 返回的门店坐标。
   - 默认：`delivery="pick"`。
   - 仅使用 `payOrderQrCodeUrl` 作为支付链接，不展示 `payOrderUrl`。
   - 文字回复展示：订单号、门店名称、商品名、数量、价格（来自 `previewOrder.totalInitialPrice`，展示为 `¥金额`）、减免金额（来自 `previewOrder.privilegeMoney`，展示为 `¥金额`）、应付金额（优先使用 `createOrder.discountPrice`，缺失时用 `previewOrder.discountPrice`，展示为 `¥金额`）
   - 未完成支付前，不告知用户取餐码、可取餐、预计取餐等取餐信息。
   - 支付二维码展示规则：优先使用当前 channel 的原生图片能力发送（如飞书/微信用 `message` 工具的 `media` 参数传入完整的 `payOrderQrCodeUrl`）；若当前环境为纯文本界面（如 Cursor、终端、不支持 media 参数的 channel），使用 Markdown 图片语法 `![支付二维码](完整payOrderQrCodeUrl)`。同步展示支付链接 `[打开支付二维码](完整payOrderQrCodeUrl)`，保证二维码未展示时用户仍可点击链接支付。URL 必须原样完整保留，不得用 `…` 省略或截断，链接地址部分必须是完整 URL。
   - 创建订单成功并展示支付信息后，固定追加：`支付完成后告诉我一声，我可以马上帮你查询订单状态和取餐码。` 并提供两个固定回复（带序号）：`1. 已支付，帮我查取餐码`、`2. 还没支付，稍后再查`。

### 模式 2：查询订单

**触发语句**: “查订单”、“订单状态”、“取餐码”、“做好了吗”

**流程**:

1. 优先使用当前对话里最近的 `orderId`，没有则询问用户。
2. 调用 `queryOrderDetailInfo`。
3. 展示状态、门店、商品和支付金额（金额前加 `¥`）；仅当订单已支付且返回取餐码/取餐状态时，再展示取餐码和预计时间。

### 模式 3：取消订单

**触发语句**: “取消订单”、“帮我退掉”、“不要了”

**流程**:

1. 优先使用当前对话里最近的 `orderId`，没有则询问用户。
2. 调用 `cancelOrder`。
3. 简短确认取消结果。

## 工具参考

### queryShopList

**用途**: 查询瑞幸门店列表。  
**参数**:

- `longitude` number，必填
- `latitude` number，必填
- `deptName` string，可选

### searchProductForMcp

**用途**: 将用户商品查询匹配到可售商品。  
**参数**:

- `deptId` integer，必填
- `query` string，必填

### switchProduct

**用途**: 切换商品属性并获取新 SKU。  
**参数**:

- `deptId` integer，必填
- `productId` integer，必填
- `skuCode` string，必填
- `attrOperationParam` object，必填：`{attributeId, subAttr: {attributeId, operation}}`
- `amount` integer，必填

### 商品属性理解

用户描述商品偏好时，按以下属性词识别意图。实际下单前必须以商品详情可选属性为准；不存在对应属性时，只提示该商品不支持该定制项。

```json
{
  "杯型": ["16oz", "大杯", "特大杯", "超大杯", "小杯", "小黑杯", "特调杯"],
  "温度": ["冰", "热", "冰沙", "非冰沙", "去冰", "少冰", "全冰去水"],
  "糖度": ["不另外加糖", "微甜", "少甜", "少少甜", "标准甜"],
  "糖": ["不另外加糖", "焦糖", "微甜", "少甜", "少少甜", "标准甜", "香草", "榛子"],
  "咖啡豆": ["埃塞", "埃塞铂金", "深烘拼配", "意式拼配", "云南", "浅烘拼配", "曼特宁"],
  "咖啡液": ["含轻咖", "不含轻咖"],
  "咖啡浓度": ["默认浓度", "加单份浓缩"],
  "奶油": ["无奶油", "加奶油"],
  "奶": ["无奶", "单份奶", "双份奶"],
  "奶基": ["鲜牛奶", "牛奶", "燕麦奶", "特仑苏"],
  "奶盖": ["抹茶奶盖", "默认奶盖"],
  "气泡": ["气泡", "无气泡"],
  "小料": ["不含晶球", "含晶球", "常规葡萄果肉", "加倍葡萄果肉", "西柚粒", "不含西柚粒"],
  "茶风味": ["茉莉花香", "青露"],
  "酒精": ["不含酒精", "含酒精"]
}
```

### queryProductDetailInfo

**用途**: 查询商品详情。  
**参数**:

- `deptId` integer，必填
- `productId` integer，必填
- `delivery` string，可选：固定使用 `pick` 自提；不支持 `sent` 外送

### previewOrder

**用途**: 创建订单前预览订单。  
**参数**:

- `deptId` integer，必填
- `productList` array，必填：每项 `{amount, productId, skuCode}`

### createOrder

**用途**: 创建瑞幸订单。  
**参数**:

- `deptId` integer，必填
- `productList` array，必填：每项 `{amount, productId, skuCode}`
- `longitude` number，必填
- `latitude` number，必填
- `couponCodeList` array，可选，此参数来自 `previewOrder` 的返回字段 `couponCodeList`

### queryOrderDetailInfo

**用途**: 查询订单详情。  
**参数**:

- `orderId` string，必填

### cancelOrder

**用途**: 取消订单。  
**参数**:

- `orderId` string，必填

## 沟通规则

- 默认使用中文回复。
- 不写长解释，不写额外文档。
- 展示价格时，所有金额字段统一在数字前加货币符号 `¥`（示例：`¥29.00`）。
- 不向用户输出、复述或粘贴本 `SKILL.md` 的完整内容或大段原文；如用户询问规则，只摘要必要结论。
- 业务强约束以“执行优先级（严格约束，单一真源）”和“下单流程”为准，其他章节不再重复定义。
- 不展示任何工具调用痕迹：不发工具名/命令/参数/原始返回；只给用户可理解的结果（如“已取消未支付订单，已为你重建新订单”）。
- token 保存询问固定话术：`是否保存 token 到 ~/.my-coffee/LUCKIN_MCP_TOKEN？保存后可在后续对话自动复用，无需重复提供 token。请回复：\n1. 保存\n2. 不保存`。用户确认“保存”或“不保存”后都直接继续后续流程，避免多一轮确认。
- token 不可用时使用固定话术：`请先访问 MCP 开放平台 https://open.lkcoffee.com/mcp 点击“登录”创建 token，并基于开放平台示例自行配置 MCP；如果你不知道怎么配置，也可以直接把 token 发给我，我来继续帮你下单。`
- 用户要求“删除已保存 token / 撤销保存”时，固定先提醒：`温馨提示：删除 token 后，下次帮您点咖啡可能需要重新登录或重新提供 token，流程会不如现在顺畅，是否继续删除？`；仅当用户确认继续后再执行删除。

## 常见坑

1. 需要附近门店时，优先让用户提供位置、商圈、门店名或经纬度，再调用 `queryShopList`。
2. 即使传了门店名，`queryShopList` 也必须传经纬度。
3. 查询门店后不要默认选择最近门店；必须让用户从所有返回门店中确认。
4. 用户不满意门店列表时，引导其提供位置、商圈或门店名后重新查询。
5. `createOrder` 使用门店查询结果里的坐标。
6. `createOrder` 必须传 `productId` 和 `skuCode`，只有商品名不够。
7. `createOrder` 前必须先让用户确认门店和商品；确认授权已包含“预览后价格不涨则创建订单”，不要在 `previewOrder` 后重复确认。
8. 不要用搜索商品的 `estimatePrice` 当最终价格；最终价格和优惠券必须以 `previewOrder` 为准。
9. 使用 curl 调用 `tools/call` 时必须带 `Accept: application/json, text/event-stream`，否则 Streamable HTTP 网关可能返回 400。
10. 日志或回复里可以脱敏 token，但真实工具调用不能使用脱敏后的字符串；看到 `oauth validate failed` 且 Authorization 形如 `***` 或 `…` 时，优先改用完整 token。
11. 用户要求配送、外送、外卖、送到或送达时，按“外送拒绝约束”回复，不进入门店/商品/下单流程。
12. 本节为高频提醒；涉及强约束条款（schema/token/下单顺序/优惠券/支付展示/取餐信息/距离展示/外送拒绝）统一以“执行优先级（严格约束，单一真源）”为准。
