"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { describeToolCall } from "@/lib/cooker";
import { summarize, TraceEvent } from "@/lib/trace";

const fmt = (n: number) => n.toLocaleString("zh-CN");
const sec = (ms: number) => `${(ms / 1000).toFixed(1)}s`;

const TOOL_STATUS: Record<string, string> = {
  ok: "成功",
  error: "出错",
  SUCCESS: "已执行",
  CANCELLED: "用户取消",
  REFUSED: "条件不满足",
  INVALID: "参数不符",
  CHANGED: "信息变化",
  FAILED: "失败",
  UNKNOWN: "结果未知",
  FROZEN: "已冻结",
};
const VERIFY: Record<string, string> = { pass: "通过", retry: "不通过，让模型重答", give_up: "仍不通过，已标注未核对" };

// 展开状态按轮次存在组件外：流式结束后前端会重新加载会话、消息组件重建，组件内状态会丢
const openTurns = new Set<string>();

/** 每轮回答下方的「执行过程」：一行汇总，展开看每一步（模型调用 / 工具 / 校验）的耗时与 token。 */
export function TracePanel({ events }: { events: TraceEvent[] }) {
  const turn = events[0]?.turn ?? "";
  const [open, setOpenState] = useState(() => openTurns.has(turn));
  const setOpen = (v: boolean) => {
    if (v) openTurns.add(turn);
    else openTurns.delete(turn);
    setOpenState(v);
  };
  if (events.length === 0) return null;
  const s = summarize(events);
  const t0 = Math.min(...events.map((e) => e.at));

  return (
    <div
      className="mt-1 w-full text-xs text-gray-500"
      data-testid="trace-panel"
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-1.5 py-1 hover:bg-gray-100"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <span>执行过程</span>
        <span>⏱ {s.seconds}s</span>
        <span>· 模型 {s.modelCalls} 次</span>
        <span>
          · 输入 {fmt(s.inputTokens)} / 输出 {fmt(s.outputTokens)} tokens
        </span>
        {s.toolCalls > 0 && <span>· 工具 {s.toolCalls} 次</span>}
        {s.retries > 0 && <span className="text-amber-600">· 校验重答 {s.retries} 次</span>}
        <span>· {s.models.join("、") || "-"}</span>
        {s.fallback && (
          <span
            className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-800"
            title="主厂商调用失败（如额度用完、限流），已自动切到备用厂商"
          >
            ⚠ 已降级到备用模型
          </span>
        )}
      </button>
      {open && (
        <ol className="mt-1 ml-2 space-y-1 border-l border-gray-200 pl-3">
          {events.map((e, i) => (
            <li
              key={i}
              className="flex flex-wrap gap-x-2"
            >
              <span className="w-12 shrink-0 text-gray-400 tabular-nums">+{(e.at - t0).toFixed(1)}s</span>
              {e.kind === "model" && (
                <>
                  <span className="font-medium text-gray-700">🧠 模型</span>
                  <span>{e.model}</span>
                  {e.fallback && <span className="text-amber-700">（{e.provider} 失败，已降级）</span>}
                  <span>{sec(e.ms)}</span>
                  <span>
                    输入 {fmt(e.input_tokens ?? 0)}
                    {e.cache_read ? `（缓存 ${fmt(e.cache_read)}）` : ""} / 输出 {fmt(e.output_tokens ?? 0)}
                  </span>
                  <span className="text-gray-400">绑定 {e.tools_bound} 个工具</span>
                  {e.forced && <span className="text-amber-700">强制调用工具</span>}
                  <span className="text-gray-400">
                    → {e.tool_calls.length ? `调用 ${e.tool_calls.join("、")}` : "作答"}
                  </span>
                </>
              )}
              {e.kind === "tool" && (
                <>
                  <span className="font-medium text-gray-700">{describeToolCall(e.name, {})}</span>
                  <span>{sec(e.ms)}</span>
                  <span className={e.status === "ok" || e.status === "SUCCESS" ? "" : "text-amber-700"}>
                    {TOOL_STATUS[e.status] ?? e.status}
                  </span>
                  <span className="text-gray-400">结果 {fmt(e.result_chars)} 字符</span>
                </>
              )}
              {e.kind === "verify" && (
                <>
                  <span className="font-medium text-gray-700">✅ 回答校验</span>
                  <span className={e.result === "pass" ? "" : "text-amber-700"}>{VERIFY[e.result]}</span>
                  {e.problems.length > 0 && <span className="text-gray-400">{e.problems.join("；")}</span>}
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
