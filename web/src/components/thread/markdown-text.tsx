"use client";

import "./markdown-styles.css";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import { FC, memo, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { SyntaxHighlighter } from "@/components/thread/syntax-highlighter";

import { TooltipIconButton } from "@/components/thread/tooltip-icon-button";
import { cn } from "@/lib/utils";
import { recipeNameFromHref } from "@/lib/cooker";
import { useRecipeSheet } from "@/components/cooker/recipe-sheet";
import { InlineImageSet, rehypeInlineImages } from "@/lib/inline-images";
import { ImageLightbox } from "@/components/cooker/image-lightbox";

import "katex/dist/katex.min.css";

interface CodeHeaderProps {
  language?: string;
  code: string;
}

const useCopyToClipboard = ({
  copiedDuration = 3000,
}: {
  copiedDuration?: number;
} = {}) => {
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const copyToClipboard = (value: string) => {
    if (!value) return;

    navigator.clipboard.writeText(value).then(() => {
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), copiedDuration);
    });
  };

  return { isCopied, copyToClipboard };
};

const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const onCopy = () => {
    if (!code || isCopied) return;
    copyToClipboard(code);
  };

  return (
    <div className="flex items-center justify-between gap-4 rounded-t-lg bg-zinc-900 px-4 py-2 text-sm font-semibold text-white">
      <span className="lowercase [&>span]:text-xs">{language}</span>
      <TooltipIconButton
        tooltip="复制"
        onClick={onCopy}
      >
        {!isCopied && <CopyIcon />}
        {isCopied && <CheckIcon />}
      </TooltipIconButton>
    </div>
  );
};

const SIZES = {
  media: "h-32 w-32",
  figure: "h-36 w-36",
  cell: "h-16 w-16",
} as const;

// 图片：rehypeInlineImages 插入的配图（data-inline-image），或回答里本来的 Markdown 图片（如瑞幸支付二维码）
function MarkdownImage({
  src,
  alt,
  className,
  ...props
}: {
  src?: string;
  alt?: string;
  className?: string;
  "data-inline-image"?: "media" | "figure" | "cell";
  "data-image-kind"?: "recipe" | "product";
  "data-recipe"?: string;
}) {
  const sheet = useRecipeSheet();
  const [zoom, setZoom] = useState(false);
  const variant = props["data-inline-image"];
  const recipe = props["data-recipe"];
  if (!variant) {
    return (
      <img
        src={src}
        alt={alt}
        loading="lazy"
        referrerPolicy="no-referrer"
        className={cn("my-3 max-h-72 max-w-[18rem] rounded-xl border border-gray-200 bg-white p-1", className)}
      />
    );
  }
  const fit = props["data-image-kind"] === "product" ? "object-contain bg-gray-50" : "object-cover";
  const img = (
    <img
      src={src}
      alt={alt}
      title={alt}
      loading="lazy"
      referrerPolicy="no-referrer" // 外部图床不带本机页面地址
      data-testid="inline-image"
      className={cn(
        "rounded-lg",
        // 页面里直接看得清：列表卡片 128px、段落下方图片行 144px、表格 64px
        SIZES[variant],
        fit,
      )}
    />
  );
  const wrap = variant === "cell" ? "mr-2 inline-block align-middle" : "block";
  // 菜谱图：点击与菜名按钮一样打开菜谱抽屉
  if (recipe && sheet) {
    return (
      <button
        type="button"
        onClick={() => sheet.openRecipe(recipe)}
        className={wrap}
        aria-label={`查看「${recipe}」做法`}
      >
        {img}
      </button>
    );
  }
  // 商品图：点击放大
  return (
    <>
      <button
        type="button"
        onClick={() => setZoom(true)}
        className={cn(wrap, "cursor-zoom-in")}
        aria-label={`放大「${alt}」图片`}
      >
        {img}
      </button>
      {zoom && src && (
        <ImageLightbox
          src={src}
          alt={alt ?? ""}
          onClose={() => setZoom(false)}
        />
      )}
    </>
  );
}

// 【菜名】被 linkifyRecipes 改写成 #recipe: 链接 → 渲染为打开菜谱抽屉的按钮；其它链接保持原样
function RecipeAwareLink({
  className,
  href,
  children,
  ...props
}: {
  className?: string;
  href?: string;
  children?: React.ReactNode;
}) {
  const sheet = useRecipeSheet();
  const recipe = recipeNameFromHref(href);
  if (recipe && sheet) {
    return (
      <button
        type="button"
        data-testid="recipe-chip"
        onClick={() => sheet.openRecipe(recipe)}
        className="mx-0.5 inline-flex items-center rounded-md bg-orange-50 px-1.5 py-0.5 font-medium text-orange-700 ring-1 ring-orange-200 hover:bg-orange-100"
      >
        {children}
      </button>
    );
  }
  return (
    <a
      className={cn("text-primary font-medium underline underline-offset-4", className)}
      href={href}
      {...props}
    >
      {children}
    </a>
  );
}

const defaultComponents: any = {
  // 聊天回答里的标题：比文章标题小一档，层级靠字重与间距区分
  h1: ({ className, ...props }: { className?: string }) => (
    <h1
      className={cn("mt-6 mb-3 text-xl font-bold first:mt-0", className)}
      {...props}
    />
  ),
  h2: ({ className, ...props }: { className?: string }) => (
    <h2
      className={cn(
        "mt-6 mb-3 border-l-4 border-orange-400 pl-2.5 text-lg font-semibold first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }: { className?: string }) => (
    <h3
      className={cn("mt-5 mb-2 text-base font-semibold first:mt-0", className)}
      {...props}
    />
  ),
  h4: ({ className, ...props }: { className?: string }) => (
    <h4
      className={cn("mt-4 mb-2 text-base font-medium text-gray-700 first:mt-0", className)}
      {...props}
    />
  ),
  h5: ({ className, ...props }: { className?: string }) => (
    <h5
      className={cn("mt-4 mb-2 font-medium first:mt-0", className)}
      {...props}
    />
  ),
  h6: ({ className, ...props }: { className?: string }) => (
    <h6
      className={cn("mt-4 mb-2 font-medium text-gray-600 first:mt-0", className)}
      {...props}
    />
  ),
  p: ({ className, ...props }: { className?: string }) => (
    <p
      className={cn("my-3 leading-7 first:mt-0 last:mb-0", className)}
      {...props}
    />
  ),
  strong: ({ className, ...props }: { className?: string }) => (
    <strong
      className={cn("font-semibold text-gray-900", className)}
      {...props}
    />
  ),
  a: RecipeAwareLink,
  img: MarkdownImage,
  // 引用块多用于提醒（过敏、未核对、待支付等）：浅琥珀底色，不用斜体
  blockquote: ({ className, ...props }: { className?: string }) => (
    <blockquote
      className={cn(
        "my-4 rounded-r-lg border-l-4 border-amber-300 bg-amber-50 px-4 py-2 text-gray-700 [&>p]:my-1",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }: { className?: string }) => (
    <ul
      className={cn("my-3 ml-5 list-disc marker:text-gray-400 [&>li]:mt-1.5", className)}
      {...props}
    />
  ),
  ol: ({ className, ...props }: { className?: string }) => (
    <ol
      className={cn("my-3 ml-5 list-decimal marker:text-gray-500 [&>li]:mt-1.5", className)}
      {...props}
    />
  ),
  hr: ({ className, ...props }: { className?: string }) => (
    <hr
      className={cn("my-6 border-gray-200", className)}
      {...props}
    />
  ),
  // 表格包一层可横向滚动的圆角外框：窄屏不撑破页面
  table: ({ className, ...props }: { className?: string }) => (
    <div className="my-4 overflow-x-auto rounded-xl border border-gray-200">
      <table
        className={cn("w-full border-collapse text-sm", className)}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }: { className?: string }) => (
    <th
      className={cn(
        "border-b border-gray-200 bg-gray-50 px-3 py-2 text-left font-medium whitespace-nowrap text-gray-600 [&[align=center]]:text-center [&[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }: { className?: string }) => (
    <td
      className={cn(
        "border-b border-gray-100 px-3 py-2.5 align-middle [&[align=center]]:text-center [&[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }: { className?: string }) => (
    <tr
      className={cn("even:bg-gray-50/60 [&:last-child>td]:border-b-0", className)}
      {...props}
    />
  ),
  sup: ({ className, ...props }: { className?: string }) => (
    <sup
      className={cn("[&>a]:text-xs [&>a]:no-underline", className)}
      {...props}
    />
  ),
  pre: ({ className, ...props }: { className?: string }) => (
    <pre
      className={cn(
        "max-w-4xl overflow-x-auto rounded-lg bg-black text-white",
        className,
      )}
      {...props}
    />
  ),
  code: ({
    className,
    children,
    ...props
  }: {
    className?: string;
    children: React.ReactNode;
  }) => {
    const match = /language-(\w+)/.exec(className || "");

    if (match) {
      const language = match[1];
      const code = String(children).replace(/\n$/, "");

      return (
        <>
          <CodeHeader
            language={language}
            code={code}
          />
          <SyntaxHighlighter
            language={language}
            className={className}
          >
            {code}
          </SyntaxHighlighter>
        </>
      );
    }

    return (
      <code
        className={cn("rounded font-semibold", className)}
        {...props}
      >
        {children}
      </code>
    );
  },
};

const MarkdownTextImpl: FC<{ children: string; images?: InlineImageSet }> = ({
  children,
  images,
}) => {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, [rehypeInlineImages, images]]}
        components={defaultComponents}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
};

export const MarkdownText = memo(MarkdownTextImpl);
