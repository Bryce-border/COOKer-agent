"use client";

import { useState } from "react";

interface Line {
  name: string;
  amount: string;
  used_in: string[];
}
export interface ShoppingListData {
  groceries: Line[];
  pantry: Line[];
  warnings: string[];
}

export function isShoppingList(v: unknown): v is ShoppingListData {
  const o = v as Record<string, unknown>;
  return !!o && Array.isArray(o.groceries) && Array.isArray(o.pantry);
}

/** make_shopping_list 工具结果的清单视图：可勾选，勾选状态只在本地页面内保存。 */
export function ShoppingList({ data }: { data: ShoppingListData }) {
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const toggle = (k: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const section = (title: string, lines: Line[], prefix: string) =>
    lines.length > 0 && (
      <div>
        <h4 className="mb-1 text-sm font-semibold text-gray-700">{title}</h4>
        <ul className="divide-y divide-gray-100">
          {lines.map((l) => {
            const k = `${prefix}:${l.name}`;
            return (
              <li key={k}>
                <label className="flex cursor-pointer items-start gap-2 py-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={checked.has(k)}
                    onChange={() => toggle(k)}
                    className="mt-1"
                  />
                  <span className={checked.has(k) ? "text-gray-400 line-through" : ""}>
                    <span className="font-medium">{l.name}</span> {l.amount}
                    <span className="ml-2 text-xs text-gray-400">{l.used_in.join("、")}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </div>
    );

  return (
    <div
      className="mx-auto w-full max-w-3xl rounded-lg border border-emerald-200 bg-emerald-50/40 p-4"
      data-testid="shopping-list"
    >
      <h3 className="mb-3 font-semibold">🛒 购物清单</h3>
      <div className="grid gap-4 sm:grid-cols-2">
        {section("需要买", data.groceries, "g")}
        {section("常备调料（家里可能有）", data.pantry, "p")}
      </div>
      {data.warnings.length > 0 && (
        <p className="mt-3 text-xs text-gray-500">{data.warnings.join("；")}</p>
      )}
    </div>
  );
}
