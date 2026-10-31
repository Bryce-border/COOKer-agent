"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useStreamContext } from "@/providers/Stream";
import { useProvider } from "./provider-select";

/** 后端 cooker.mcd.actions.run_write 发出的 interrupt：麦当劳写操作确认卡。
 *  卡片内容（金额、消耗、订单状态）由后端用实时查询生成，不是模型写的文字。 */
export interface McdConfirmValue {
  type: "mcd_confirm";
  tool: string;
  title: string;
  rows: [string, string][];
  warnings: string[];
  fingerprint: string;
}

export function isMcdConfirm(v: unknown): v is McdConfirmValue {
  const o = v as Record<string, unknown> | null;
  return (
    typeof o === "object" &&
    o !== null &&
    o.type === "mcd_confirm" &&
    Array.isArray(o.rows) &&
    typeof o.fingerprint === "string"
  );
}

const EMPHASIS = new Set(["实付", "本次消耗"]);

export function McdConfirm({ value }: { value: McdConfirmValue }) {
  const stream = useStreamContext();
  const [provider] = useProvider();
  const [sent, setSent] = useState<"confirm" | "cancel" | null>(null);

  const answer = (confirmed: boolean) => {
    setSent(confirmed ? "confirm" : "cancel"); // 防止重复点击导致多次恢复（后端台账也会兜底）
    // 指纹原样带回：恢复时工具会重新查询，若与卡片不一致就不执行
    stream.submit(undefined, {
      command: { resume: { confirmed, fingerprint: value.fingerprint } },
      context: { provider },
      streamMode: ["values"],
      streamSubgraphs: true,
      streamResumable: true,
    });
  };

  return (
    <div
      className="w-full max-w-md overflow-hidden rounded-xl border border-amber-300 bg-white shadow-sm"
      data-testid="mcd-confirm"
    >
      <div className="flex items-center gap-2 bg-amber-400 px-4 py-2 text-sm font-semibold text-amber-950">
        <span aria-hidden>🍟</span>
        {value.title}
      </div>
      <dl className="divide-y divide-gray-100 px-4 text-sm">
        {value.rows.map(([label, text]) => (
          <div
            key={label}
            className="flex gap-3 py-2"
          >
            <dt className="w-24 shrink-0 text-gray-500">{label}</dt>
            <dd
              className={
                EMPHASIS.has(label)
                  ? "font-semibold whitespace-pre-line text-red-600"
                  : "whitespace-pre-line text-gray-900"
              }
            >
              {text}
            </dd>
          </div>
        ))}
      </dl>
      {value.warnings.length > 0 && (
        <ul className="space-y-1 bg-amber-50 px-4 py-2 text-xs text-amber-900">
          {value.warnings.map((w) => (
            <li key={w}>⚠️ {w}</li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2 px-4 py-3">
        <Button
          size="sm"
          disabled={sent !== null}
          onClick={() => answer(true)}
        >
          {sent === "confirm" ? "执行中…" : "确认执行"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={sent !== null}
          onClick={() => answer(false)}
        >
          取消
        </Button>
        <span className="ml-auto text-xs text-gray-400">
          仅执行一次，结果不明不会自动重试
        </span>
      </div>
    </div>
  );
}
