"use client";

import { useMemo } from "react";
import { useStreamContext } from "@/providers/Stream";
import { mentionedImages, turnImages } from "@/lib/cooker";

const MAX_IMAGES = 6;

/** 回答下方的商品图片条（麦当劳、瑞幸）：取本轮工具结果 artifact 里的商品图，只显示回答中提到的商品。
 *  图片地址由后端从官方返回中提取并过域名白名单，不经过模型。 */
export function ProductImages({
  messageId,
  text,
  enabled,
}: {
  messageId?: string;
  text: string;
  enabled: boolean;
}) {
  const { messages } = useStreamContext();
  const images = useMemo(
    () =>
      enabled
        ? mentionedImages(text, turnImages(messages, messageId)).slice(0, MAX_IMAGES)
        : [],
    [enabled, text, messages, messageId],
  );
  if (images.length === 0) return null;
  return (
    <div
      className="flex gap-3 overflow-x-auto pb-1"
      data-testid="product-images"
    >
      {images.map((img) => (
        <figure
          key={img.url}
          className="w-32 shrink-0 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm"
        >
          <img
            src={img.url}
            alt={img.name}
            loading="lazy"
            referrerPolicy="no-referrer" // 加载外部图床时不带本机页面地址
            className="h-28 w-full bg-gray-50 object-contain"
          />
          <figcaption className="truncate px-2 py-1.5 text-xs text-gray-700">
            {img.name}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
