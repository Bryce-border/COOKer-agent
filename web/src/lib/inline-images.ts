// 把图片放到「对应的信息」旁边：rehype 插件在 Markdown 渲染前改写语法树（hast）。
//
// - 列表项：若它第一个提到的菜 / 商品有图 → 卡片（左侧大图，.cooker-media）；同一项里其余有图的名称 → 项末尾的图片行
//   第一个名称没有图时，不能把后面名称的图放到最左边（会被误认为是第一个名称的图）→ 全部放到项末尾的图片行
//   所在列表加 .cooker-media-list，没图的项显示「暂无图片」占位，保持版式整齐
// - 段落、标题：提到的每个有图名称，在这一段下方的图片行里展示（带名称）
// - 表格单元格：在名称前插入图片
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

type Variant = "media" | "figure" | "cell";

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
  first: boolean; // 是不是这个块里第一个出现的名称
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

function el(tagName: string, className: string[], children: HastNode[] = [], extra: Record<string, unknown> = {}): HastNode {
  return { type: "element", tagName, properties: { className, ...extra }, children };
}

function image(m: Match, variant: Variant): HastNode {
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

/** 一行带名称的图片（段落下方 / 列表项末尾） */
function figures(matches: Match[]): HastNode {
  return el("div", ["cooker-figures"], matches.map((m) =>
    el("figure", ["cooker-figure"], [image(m, "figure"), el("figcaption", [], [{ type: "text", value: m.name }])])));
}

function addClass(node: HastNode, name: string) {
  const cls = (node.properties?.className as string[] | undefined) ?? [];
  if (!cls.includes(name)) node.properties = { ...node.properties, className: [...cls, name] };
}

export function rehypeInlineImages(images?: InlineImageSet) {
  return (tree: HastNode) => {
    if (!images) return;
    const keys = productKeys(images.products);
    const used = new Set<string>();

    /** 块「自身内容」里所有可配图的名称（文档顺序；每个名称在整条回答中只取第一次） */
    const findAll = (block: HastNode): Match[] => {
      const out: Match[] = [];
      let seen = false; // 前面是否已出现过别的名称（含没图 / 已配过图的）
      const walk = (node: HastNode) => {
        (node.children ?? []).forEach((child, i) => {
          if (child.type === "element") {
            const recipe = recipeOf(child);
            if (recipe) {
              if (images.recipes[recipe] && !used.has(recipe)) {
                used.add(recipe);
                out.push({ kind: "recipe", name: recipe, url: images.recipes[recipe], parent: node, index: i, first: !seen });
              }
              seen = true;
              return; // 菜名按钮里的文字不再当商品名匹配
            }
            if (!SKIP.has(child.tagName ?? "")) walk(child);
          } else if (child.type === "text" && child.value) {
            // 文本里可能有多个商品名：按出现位置依次取，被长名称覆盖的位置不再匹配短名称
            const hits: { at: number; end: number; img: ProductImage }[] = [];
            for (const { key, img } of keys) {
              let at = child.value.indexOf(key);
              while (at >= 0) {
                const end = at + key.length;
                if (!hits.some((h) => at < h.end && end > h.at)) hits.push({ at, end, img });
                at = child.value.indexOf(key, end);
              }
            }
            for (const h of hits.sort((a, b) => a.at - b.at)) {
              if (!used.has(h.img.name)) {
                used.add(h.img.name);
                out.push({ kind: "product", name: h.img.name, url: h.img.url, parent: node, index: i, offset: h.at, first: !seen });
              }
              seen = true;
            }
          }
        });
      };
      walk(block);
      return out;
    };

    /** 在名称前插图（表格单元格）。倒序处理：先切后面的文本节点，前面的位置不受影响 */
    const insertBefore = (matches: Match[], variant: Variant) => {
      for (const m of [...matches].reverse()) {
        const siblings = m.parent.children!;
        if (m.kind === "recipe" || !m.offset) {
          siblings.splice(m.index, 0, image(m, variant));
        } else {
          const text = siblings[m.index].value!;
          siblings.splice(m.index, 1,
            { type: "text", value: text.slice(0, m.offset) },
            image(m, variant),
            { type: "text", value: text.slice(m.offset) });
        }
      }
    };

    const visit = (node: HastNode, inBlock: boolean) => {
      const children = node.children ?? [];
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (child.type !== "element") continue;
        const tag = child.tagName ?? "";
        if (!BLOCKS.has(tag) || (tag === "p" && inBlock)) {
          if (tag !== "pre" && tag !== "code") visit(child, inBlock);
          continue;
        }
        const matches = findAll(child);
        visit(child, true); // 嵌套列表先处理，避免下面改写结构后重复遍历
        if (matches.length === 0) continue;
        if (tag === "li") {
          addClass(node, "cooker-media-list");
          const [lead, ...rest] = matches[0].first ? matches : [null, ...matches];
          const body: HastNode[] = [...(child.children ?? [])];
          if (rest.length) body.push(figures(rest as Match[]));
          if (lead) {
            addClass(child, "cooker-media");
            child.children = [image(lead, "media"), el("div", ["cooker-media-body"], body)];
          } else {
            child.children = [el("div", ["cooker-media-body"], body)];
          }
        } else if (tag === "td") {
          insertBefore(matches, "cell");
        } else {
          children.splice(i + 1, 0, figures(matches)); // 段落 / 标题下方
          i++;
        }
      }
      // 卡片列表里没配图的项也包一层正文容器：li 是两列网格（图 / 正文），零散的文字节点不能各占一格
      const cls = (node.properties?.className as string[] | undefined) ?? [];
      if (cls.includes("cooker-media-list")) {
        for (const li of children) {
          const wrapped = li.children?.some((c) =>
            (c.properties?.className as string[] | undefined)?.includes("cooker-media-body"));
          if (li.tagName === "li" && !wrapped) li.children = [el("div", ["cooker-media-body"], li.children ?? [])];
        }
      }
    };
    visit(tree, false);
  };
}
