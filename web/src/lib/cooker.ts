// COOKer 前端的共用约定与工具函数

// 后端约定：回答里库内菜名写成【菜名】（见 src/cooker/agent/prompts.py）
const CITE_RE = /【([^】\n]{1,20})】/g;
export const RECIPE_HREF_PREFIX = "#recipe:";

/** 把【菜名】改写成 Markdown 链接，交给 MarkdownText 的自定义 a 组件渲染成菜谱按钮。
 *  用 # 锚点而不是自定义协议：react-markdown 默认会清洗非 http(s) 协议的链接。 */
export function linkifyRecipes(text: string): string {
  return text.replace(
    CITE_RE,
    (_, name: string) =>
      `[${name}](${RECIPE_HREF_PREFIX}${encodeURIComponent(name)})`,
  );
}

/** 回答里引用的库内菜名（去重，保持出现顺序）。 */
export function citedRecipes(text: string): string[] {
  return [...new Set([...text.matchAll(CITE_RE)].map((m) => m[1]))];
}

export function recipeNameFromHref(href?: string): string | null {
  if (!href?.startsWith(RECIPE_HREF_PREFIX)) return null;
  try {
    return decodeURIComponent(href.slice(RECIPE_HREF_PREFIX.length));
  } catch {
    return null;
  }
}

export type Provider = "qwen" | "deepseek";
export const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "qwen", label: "通义千问" },
  { value: "deepseek", label: "DeepSeek" },
];

export interface RecipeDetail {
  summary: {
    id: string;
    name: string;
    category: string;
    difficulty: number;
    calories: number | null;
    time_minutes: number | null;
    main_ingredients: string[];
    flavors: string[];
    allergens: string[];
  };
  description: string;
  ingredients: string[];
  calculation: string;
  steps: { title: string | null; steps: string[] }[];
  extras: string;
  images: string[];
  source: string;
  notice: string;
}

export async function fetchRecipe(
  apiUrl: string,
  name: string,
  signal?: AbortSignal,
): Promise<RecipeDetail> {
  const res = await fetch(
    `${apiUrl}/cooker/recipes?name=${encodeURIComponent(name)}`,
    { signal },
  );
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? `请求失败（${res.status}）`);
  return data as RecipeDetail;
}

export interface RecipeCover {
  query: string; // 回答里写的菜名
  name: string; // 菜谱库里的正式名
  image: string;
}

// 页面级缓存：同一道菜在多条回答里出现时只请求一次；null 表示查过但没有图
const coverCache = new Map<string, RecipeCover | null>();
// 请求中的菜名 → 对应的请求。并发调用（多条回答同时渲染、开发模式 effect 执行两次）共用同一个请求
const pending = new Map<string, Promise<void>>();

/** 批量取封面（GET /cooker/covers，一次最多 8 个），只请求缓存里没有、也不在请求中的菜名。
 *  共享请求不接受 AbortSignal：一个调用方取消不应让其他调用方失败；调用方自行忽略过期结果。 */
export async function fetchCovers(
  apiUrl: string,
  names: string[],
): Promise<RecipeCover[]> {
  const missing = names
    .filter((n) => !coverCache.has(n) && !pending.has(n))
    .slice(0, 8);
  if (missing.length > 0) {
    const qs = missing.map((n) => `names=${encodeURIComponent(n)}`).join("&");
    const request = fetch(`${apiUrl}/cooker/covers?${qs}`)
      .then(async (res) => {
        if (!res.ok) throw new Error(`封面请求失败（${res.status}）`);
        const { covers } = (await res.json()) as { covers: RecipeCover[] };
        missing.forEach((n) => coverCache.set(n, null));
        covers.forEach((c) => coverCache.set(c.query, c));
      })
      .finally(() => missing.forEach((n) => pending.delete(n))); // 失败不写缓存，下次可重试
    missing.forEach((n) => pending.set(n, request));
  }
  await Promise.all(names.map((n) => pending.get(n)));
  return names
    .map((n) => coverCache.get(n))
    .filter((c): c is RecipeCover => !!c);
}

export function imageUrl(apiUrl: string, src: string): string {
  return src.startsWith("http") ? src : `${apiUrl}${src}`;
}

// 工具名 → 界面上的中文说明
const TOOL_LABELS: Record<string, { icon: string; label: string; arg?: string }> = {
  search_recipes: { icon: "🔍", label: "搜索菜谱", arg: "query" },
  get_recipe: { icon: "📖", label: "查看菜谱", arg: "name" },
  recommend_by_ingredients: { icon: "🥕", label: "按食材推荐", arg: "have" },
  browse_recipes: { icon: "🎲", label: "筛选菜谱" },
  scale_recipe: { icon: "⚖️", label: "换算用量", arg: "name" },
  make_shopping_list: { icon: "🛒", label: "生成购物清单" },
  search_cooking_tips: { icon: "💡", label: "查烹饪技巧", arg: "query" },
  update_profile: { icon: "📝", label: "更新偏好" },
  get_profile: { icon: "👤", label: "读取偏好" },
  load_skill: { icon: "🧩", label: "加载技能", arg: "name" },
  read_skill_file: { icon: "📄", label: "读取技能文档", arg: "path" },
};

// 麦当劳 MCP 工具（工具名与官方服务一致，见 src/cooker/mcd/tool_schemas.json）
const MCD_LABELS: Record<string, string> = {
  "now-time-info": "当前时间",
  "query-my-account": "查积分账户",
  "query-lottery-info": "查抽奖活动",
  "query-my-prizes": "查中奖记录",
  "query-mall-products": "查积分商城",
  "mall-product-detail": "查商城商品",
  "mall-gift-products": "查麦有礼",
  "mall-order-list": "查商城订单",
  "mall-order-detail": "查商城订单详情",
  "available-coupons": "查可领券",
  "query-my-coupons": "查我的卡包",
  "query-store-coupons": "查门店可用券",
  "campaign-calendar": "查活动日历",
  "query-promotions": "查团餐优惠",
  "query-nearby-stores": "找门店",
  "delivery-query-stores": "找外送门店",
  "delivery-query-addresses": "查收货地址",
  "query-meals": "看菜单",
  "query-meal-detail": "看餐品详情",
  "query-meal-assistance": "查团餐助餐",
  "calculate-price": "算价",
  "order-list": "查订单列表",
  "query-order": "查订单",
  "query-survey-coupon": "查满意度奖券",
  "list-nutrition-foods": "查营养",
  "query-party-city": "查活动城市",
  "query-party-store": "查活动门店",
  "query-party-store-date": "查活动日期",
  "query-party-store-session": "查活动场次",
  "create-order": "下单（需确认）",
  "cancel-order": "取消订单（需确认）",
  "mall-create-order": "积分兑换（需确认）",
  "party-order-create": "主题活动预约（需确认）",
  "draw-lottery": "抽奖（需确认）",
  "auto-bind-coupons": "一键领券（需确认）",
  "delivery-create-address": "新增地址（需确认）",
};

export function describeToolCall(name: string, args: Record<string, unknown>): string {
  const meta = TOOL_LABELS[name];
  if (!meta) return MCD_LABELS[name] ? `🍟 ${MCD_LABELS[name]}` : `🔧 ${name}`;
  let detail = "";
  if (meta.arg && args[meta.arg] !== undefined) {
    const v = args[meta.arg];
    detail = Array.isArray(v) ? v.join("、") : String(v);
  } else if (name === "make_shopping_list" && Array.isArray(args.dishes)) {
    detail = (args.dishes as { name: string }[]).map((d) => d.name).join("、");
  }
  if (name === "scale_recipe" && args.people) detail += ` × ${args.people} 人`;
  return `${meta.icon} ${meta.label}${detail ? `：${detail}` : ""}`;
}
