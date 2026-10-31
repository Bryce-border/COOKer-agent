"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";

/** 点击商品图后的放大预览：铺满屏幕的半透明遮罩，点任意处或按 Esc 关闭。
 *  用 portal 挂到 body，避免嵌在段落 / 表格里时出现非法的 DOM 嵌套。 */
export function ImageLightbox({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div
      role="dialog"
      aria-label={`${alt}（大图）`}
      data-testid="image-lightbox"
      onClick={onClose}
      className="fixed inset-0 z-50 flex cursor-zoom-out flex-col items-center justify-center gap-3 bg-black/70 p-6"
    >
      <img
        src={src}
        alt={alt}
        referrerPolicy="no-referrer"
        className="max-h-[75vh] max-w-[90vw] rounded-xl bg-white object-contain p-2 shadow-2xl"
      />
      <span className="rounded-full bg-black/50 px-3 py-1 text-sm text-white">{alt}</span>
    </div>,
    document.body,
  );
}
