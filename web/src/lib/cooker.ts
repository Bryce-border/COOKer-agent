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
};

export function describeToolCall(name: string, args: Record<string, unknown>): string {
  const meta = TOOL_LABELS[name];
  if (!meta) return `🔧 ${name}`;
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
