"use client";

import { useEffect, useMemo, useState } from "react";
import { useStreamContext } from "@/providers/Stream";
import {
  citedRecipes,
  fetchCovers,
  imageUrl,
  RecipeCover,
} from "@/lib/cooker";
import { useRecipeSheet } from "./recipe-sheet";

const MAX_COVERS = 6;

/** 回答下方的菜品图片条：回答里每个带图的【菜名】显示一张封面，点击打开菜谱抽屉。
 *  图片来自菜谱库原文（GET /cooker/covers），不经过模型，不会出现编造的图片地址。
 *  enabled=false（回答还在流式输出）时不请求，避免菜名逐字增长时反复发请求。 */
export function RecipeCovers({
  text,
  enabled,
}: {
  text: string;
  enabled: boolean;
}) {
  const { apiUrl } = useStreamContext();
  const sheet = useRecipeSheet();
  const key = useMemo(
    () => citedRecipes(text).slice(0, MAX_COVERS).join("\n"),
    [text],
  );
  const [covers, setCovers] = useState<RecipeCover[]>([]);

  useEffect(() => {
    if (!enabled || !key) return;
    let stale = false; // 菜名变化或组件卸载后，忽略迟到的结果
    fetchCovers(apiUrl, key.split("\n"))
      .then((c) => !stale && setCovers(c))
      .catch(() => {}); // 图片只是锦上添花：失败时不显示，不打扰对话
    return () => {
      stale = true;
    };
  }, [apiUrl, key, enabled]);

  if (!enabled || covers.length === 0) return null;
  return (
    <div
      className="flex gap-3 overflow-x-auto pb-1"
      data-testid="recipe-covers"
    >
      {covers.map((c) => (
        <button
          key={c.name}
          type="button"
          onClick={() => sheet?.openRecipe(c.name)}
          className="group w-36 shrink-0 overflow-hidden rounded-lg border border-gray-200 bg-white text-left shadow-sm transition hover:shadow-md"
          title={`查看「${c.name}」做法`}
        >
          <img
            src={imageUrl(apiUrl, c.image)}
            alt={c.name}
            loading="lazy"
            className="h-24 w-full object-cover transition group-hover:scale-105"
          />
          <span className="block truncate px-2 py-1.5 text-xs text-gray-700">
            {c.name}
          </span>
        </button>
      ))}
    </div>
  );
}
