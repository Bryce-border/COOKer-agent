// 把图片放到「对应的信息」旁边：rehype 插件在 Markdown 渲染前改写语法树（hast）。
//
// - 列表项：若它「第一个」提到的菜 / 商品有图，改成左图右文（.cooker-media），所在列表加 .cooker-media-list 统一样式；
//   第一个名称没有图（或已配过）时不能把后面某个名称的图放到最左边，否则看起来像前一个名称的图 → 改为在名称前插小图
// - 表格单元格、标题、段落：在名称前插入一张小图
// 图片地址全部来自代码（菜谱封面接口、工具结果的 artifact），模型只写名称；每张图每条回答只出现一次。

import type { ProductImage } from "./cooker";
import { RECIPE_HREF_PREFIX } from "./cooker";

export interface InlineImageSet {
  recipes: Record<string, string>; // 菜名 → 封面地址（已带 apiUrl）
  products: ProductImage[]; // 本轮工具结果里的商品图
}

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

const BLOCKS = new Set(["li", "td", "h1", "h2", "h3", "h4", "p"]);
// 找名称时不进入这些子树：嵌套列表 / 表格各自处理，代码块里的文字不配图
const SKIP = new Set(["ul", "ol", "table", "pre", "code"]);

interface Match {
  kind: "recipe" | "product";
  name: string;
  url: string;
  parent: HastNode;
  index: number; // 名称所在节点在 parent.children 中的位置
  offset?: number; // 商品名在文本节点中的起始位置
  first: boolean; // 是不是这个块里第一个出现的名称（只有这时列表项才用左图右文）
}

/** 「生椰拿铁（原创）」也匹配「生椰拿铁」：模型常省略括号后缀。长的先匹配，避免「拿铁」抢走「生椰拿铁」。 */
function productKeys(products: ProductImage[]) {
  const keys: { key: string; img: ProductImage }[] = [];
  for (const img of products) {
    keys.push({ key: img.name, img });
    const base = img.name.replace(/[（(][^）)]*[）)]\s*$/, "").trim();
    if (base.length >= 2 && base !== img.name) keys.push({ key: base, img });
  }
  return keys.sort((a, b) => b.key.length - a.key.length);
}

function recipeOf(node: HastNode): string | null {
  const href = node.properties?.href;
  if (node.tagName !== "a" || typeof href !== "string" || !href.startsWith(RECIPE_HREF_PREFIX)) return null;
  try {
    return decodeURIComponent(href.slice(RECIPE_HREF_PREFIX.length));
  } catch {
    return null;
  }
}

function thumb(m: Match, variant: "media" | "inline"): HastNode {
  return {
    type: "element",
    tagName: "img",
    properties: {
      src: m.url,
      alt: m.name,
      dataInlineImage: variant,
      dataImageKind: m.kind,
      ...(m.kind === "recipe" ? { dataRecipe: m.name } : {}),
    },
    children: [],
  };
}

export function rehypeInlineImages(images?: InlineImageSet) {
  return (tree: HastNode) => {
    if (!images) return;
    const keys = productKeys(images.products);
    const used = new Set<string>();

    // 在一个块的「自身内容」里找第一个可配图的名称（文档顺序）。state.seen：前面是否已出现过别的名称
    const findIn = (node: HastNode, state = { seen: false }): Match | null => {
      for (let i = 0; i < (node.children ?? []).length; i++) {
        const child = node.children![i];
        if (child.type === "element") {
          const recipe = recipeOf(child);
          if (recipe) {
            if (images.recipes[recipe] && !used.has(recipe))
              return { kind: "recipe", name: recipe, url: images.recipes[recipe], parent: node, index: i, first: !state.seen };
            state.seen = true; // 没有图或已配过的菜名也算「出现过」
            continue; // 菜名按钮里的文字不再当商品名匹配
          }
          if (SKIP.has(child.tagName ?? "")) continue;
          const inner = findIn(child, state);
          if (inner) return inner;
        } else if (child.type === "text" && child.value) {
          let best: Match | null = null;
          let earliest = Infinity; // 任意商品名（含已配过图的）最早出现的位置
          for (const { key, img } of keys) {
            const at = child.value.indexOf(key);
            if (at < 0) continue;
            earliest = Math.min(earliest, at);
            if (!used.has(img.name) && (!best || at < best.offset!))
              best = { kind: "product", name: img.name, url: img.url, parent: node, index: i, offset: at, first: false };
          }
          if (best) return { ...best, first: !state.seen && earliest === best.offset };
          if (earliest < Infinity) state.seen = true;
        }
      }
      return null;
    };

    const addClass = (node: HastNode, name: string) => {
      const cls = (node.properties?.className as string[] | undefined) ?? [];
      if (!cls.includes(name)) node.properties = { ...node.properties, className: [...cls, name] };
    };

    const apply = (block: HastNode, m: Match, parent: HastNode) => {
      used.add(m.name);
      if (block.tagName === "li" && m.first) {
        addClass(parent, "cooker-media-list"); // 同一列表里没配图的项也用卡片样式，文字对齐
        addClass(block, "cooker-media");
        block.children = [
          thumb(m, "media"),
          { type: "element", tagName: "div", properties: { className: ["cooker-media-body"] }, children: block.children },
        ];
        return;
      }
      const siblings = m.parent.children!;
      if (m.kind === "recipe" || !m.offset) {
        siblings.splice(m.index, 0, thumb(m, "inline"));
      } else {
        // 在文本节点中间插入：前半段文字 + 小图 + 从名称开始的后半段
        const text = siblings[m.index].value!;
        siblings.splice(m.index, 1,
          { type: "text", value: text.slice(0, m.offset) },
          thumb(m, "inline"),
          { type: "text", value: text.slice(m.offset) });
      }
    };

    const visit = (node: HastNode, inBlock: boolean) => {
      for (const child of [...(node.children ?? [])]) {
        if (child.type !== "element") continue;
        const tag = child.tagName ?? "";
        if (BLOCKS.has(tag) && !(tag === "p" && inBlock)) {
          const m = findIn(child);
          if (m) apply(child, m, node);
          visit(child, true); // 继续处理嵌套列表
        } else if (tag !== "pre" && tag !== "code") {
          visit(child, inBlock);
        }
      }
    };
    visit(tree, false);
  };
}
