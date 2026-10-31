"use client";

import { useEffect, useMemo, useState } from "react";
import { useStreamContext } from "@/providers/Stream";
import { citedRecipes, fetchCovers, imageUrl } from "@/lib/cooker";

const MAX_COVERS = 8;

/** 回答里【菜名】的封面：菜名 → 图片地址（已带 apiUrl）。图片在 Markdown 渲染时插到对应菜名旁（lib/inline-images.ts）。
 *  enabled=false（回答还在流式输出）时不请求，避免菜名逐字增长时反复发请求。 */
export function useRecipeCovers(text: string, enabled: boolean): Record<string, string> {
  const { apiUrl } = useStreamContext();
  const key = useMemo(() => citedRecipes(text).slice(0, MAX_COVERS).join("\n"), [text]);
  const [covers, setCovers] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!enabled || !key) return;
    let stale = false; // 菜名变化或组件卸载后，忽略迟到的结果
    fetchCovers(apiUrl, key.split("\n"))
      .then((list) => {
        if (stale) return;
        // 回答里写的名字（query）可能是同义名，按它建索引才能和【】里的文字对上
        setCovers(Object.fromEntries(list.map((c) => [c.query, imageUrl(apiUrl, c.image)])));
      })
      .catch(() => {}); // 图片只是锦上添花：失败时不显示，不打扰对话
    return () => {
      stale = true;
    };
  }, [apiUrl, key, enabled]);

  return enabled ? covers : {};
}
