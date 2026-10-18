"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useStreamContext } from "@/providers/Stream";
import { useProvider } from "./provider-select";

export interface ConfirmInterruptValue {
  type: "confirm";
  question: string;
}

export function isConfirmInterrupt(v: unknown): v is ConfirmInterruptValue {
  return (
    typeof v === "object" &&
    v !== null &&
    (v as Record<string, unknown>).type === "confirm" &&
    typeof (v as Record<string, unknown>).question === "string"
  );
}

/** 后端 update_profile 中 interrupt({"type":"confirm", ...}) 的界面。
 *  点击后以 Command(resume=…) 从断点恢复图：工具会从头重新执行，这次 interrupt() 返回用户的选择。 */
export function ConfirmInterrupt({ value }: { value: ConfirmInterruptValue }) {
  const stream = useStreamContext();
  const [provider] = useProvider();
  const [sent, setSent] = useState(false);

  const answer = (resume: "是" | "否") => {
    setSent(true); // 防止重复点击导致多次恢复
    stream.submit(undefined, {
      command: { resume },
      context: { provider },
      streamMode: ["values"],
      streamSubgraphs: true,
      streamResumable: true,
    });
  };

  return (
    <div
      className="rounded-lg border border-amber-300 bg-amber-50 p-4"
      data-testid="confirm-interrupt"
    >
      <p className="mb-3 text-sm font-medium text-amber-900">⚠️ 需要你确认</p>
      <p className="mb-4 text-sm text-amber-900">{value.question}</p>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={sent}
          onClick={() => answer("是")}
        >
          确认
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={sent}
          onClick={() => answer("否")}
        >
          取消
        </Button>
      </div>
    </div>
  );
}
