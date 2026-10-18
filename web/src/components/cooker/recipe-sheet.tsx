"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useStreamContext } from "@/providers/Stream";
import { fetchRecipe, imageUrl, RecipeDetail } from "@/lib/cooker";
import { MarkdownText } from "@/components/thread/markdown-text";

type RecipeSheetContextType = { openRecipe: (name: string) => void };
const RecipeSheetContext = createContext<RecipeSheetContextType | null>(null);

export function useRecipeSheet() {
  return useContext(RecipeSheetContext);
}

/** 全局唯一的菜谱抽屉：任何地方调用 openRecipe(菜名) 即可打开。
 *  onAsk：把一句话作为用户消息发给 Agent（由 Thread 提供）。 */
export function RecipeSheetProvider({
  children,
  onAsk,
}: {
  children: ReactNode;
  onAsk: (text: string) => void;
}) {
  const [name, setName] = useState<string | null>(null);
  const openRecipe = useCallback((n: string) => setName(n), []);

  return (
    <RecipeSheetContext.Provider value={{ openRecipe }}>
      {children}
      <Sheet
        open={name !== null}
        onOpenChange={(open) => !open && setName(null)}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          {name && (
            <RecipeView
              key={name} // 换菜谱时重新挂载：加载状态、人数输入自然重置
              name={name}
              onAsk={(text) => {
                setName(null);
                onAsk(text);
              }}
            />
          )}
        </SheetContent>
      </Sheet>
    </RecipeSheetContext.Provider>
  );
}

function RecipeView({ name, onAsk }: { name: string; onAsk: (t: string) => void }) {
  const { apiUrl } = useStreamContext();
  const [recipe, setRecipe] = useState<RecipeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState("2");

  useEffect(() => {
    const ctrl = new AbortController();
    fetchRecipe(apiUrl, name, ctrl.signal)
      .then(setRecipe)
      .catch((e) => !ctrl.signal.aborted && setError(String(e.message ?? e)));
    return () => ctrl.abort(); // 快速切换菜谱时取消上一个请求，避免旧结果覆盖新结果
  }, [apiUrl, name]);

  if (error)
    return (
      <SheetHeader>
        <SheetTitle>{name}</SheetTitle>
        <SheetDescription>{error}</SheetDescription>
      </SheetHeader>
    );
  if (!recipe)
    return (
      <SheetHeader>
        <SheetTitle>{name}</SheetTitle>
        <SheetDescription>加载中…</SheetDescription>
      </SheetHeader>
    );

  const s = recipe.summary;
  const peopleNum = Math.max(1, Math.min(100, parseInt(people) || 1));
  return (
    <div
      className="flex flex-col gap-4 px-4 pb-8"
      data-testid="recipe-sheet"
    >
      <SheetHeader className="px-0">
        <SheetTitle className="text-2xl">{s.name}</SheetTitle>
        <SheetDescription>
          {s.category} · 难度 {"★".repeat(s.difficulty)}
          {s.calories ? ` · 约 ${s.calories} 大卡` : ""}
          {s.time_minutes ? ` · 约 ${s.time_minutes} 分钟` : ""}
        </SheetDescription>
      </SheetHeader>

      {recipe.images.length > 0 && (
        <div className="flex gap-2 overflow-x-auto">
          {recipe.images.slice(0, 4).map((src) => (
            <img
              key={src}
              src={imageUrl(apiUrl, src)}
              alt={s.name}
              loading="lazy"
              className="h-40 rounded-lg object-cover"
            />
          ))}
        </div>
      )}

      {recipe.description && <p className="text-sm leading-6">{recipe.description}</p>}

      {s.allergens.length > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          可能含过敏原：{s.allergens.join("、")}（{recipe.notice}）
        </p>
      )}

      <section>
        <h3 className="mb-2 font-semibold">原料和工具</h3>
        <div className="flex flex-wrap gap-1.5">
          {recipe.ingredients.map((i) => (
            <span
              key={i}
              className="rounded-full bg-gray-100 px-2.5 py-0.5 text-sm"
            >
              {i}
            </span>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 font-semibold">用量</h3>
        <div className="text-sm">
          <MarkdownText>{recipe.calculation}</MarkdownText>
        </div>
      </section>

      <section>
        <h3 className="mb-2 font-semibold">步骤</h3>
        {recipe.steps.map((g, gi) => (
          <div
            key={gi}
            className="mb-3"
          >
            {g.title && <h4 className="mb-1 text-sm font-medium text-gray-600">{g.title}</h4>}
            <ol className="list-decimal space-y-1 pl-5 text-sm leading-6">
              {g.steps.map((step, si) => (
                <li key={si}>{step}</li>
              ))}
            </ol>
          </div>
        ))}
      </section>

      <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t bg-white pt-3">
        <Input
          type="number"
          min={1}
          max={100}
          value={people}
          onChange={(e) => setPeople(e.target.value)}
          className="w-20"
          aria-label="人数"
        />
        <span className="text-sm">人</span>
        <Button
          size="sm"
          onClick={() => onAsk(`【${s.name}】做 ${peopleNum} 人份需要准备多少材料？`)}
        >
          换算用量
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onAsk(`【${s.name}】有什么要注意的？新手容易失败在哪？`)}
        >
          问做法要点
        </Button>
      </div>
      <p className="text-xs text-gray-400">来源：HowToCook / {recipe.source}</p>
    </div>
  );
}
