// 执行过程（后端 src/cooker/agent/trace.py 写入图状态 trace）：类型与汇总

export type TraceEvent =
  | {
      kind: "model";
      turn: string | null;
      at: number;
      ms: number;
      provider: string;
      model: string | null;
      fallback: boolean;
      input_tokens: number | null;
      output_tokens: number | null;
      cache_read: number | null;
      tools_bound: number;
      tool_calls: string[];
      forced: boolean;
    }
  | { kind: "tool"; turn: string | null; at: number; ms: number; name: string; status: string; result_chars: number }
  | { kind: "verify"; turn: string | null; at: number; ms: number; result: "pass" | "retry" | "give_up"; problems: string[] };

export interface TraceSummary {
  seconds: number;
  modelCalls: number;
  inputTokens: number;
  outputTokens: number;
  toolCalls: number;
  models: string[];
  fallback: boolean;
  retries: number;
}

export function summarize(events: TraceEvent[]): TraceSummary {
  const models = events.filter((e) => e.kind === "model");
  const start = Math.min(...events.map((e) => e.at));
  const end = Math.max(...events.map((e) => e.at + e.ms / 1000));
  return {
    seconds: events.length ? Math.round((end - start) * 10) / 10 : 0,
    modelCalls: models.length,
    inputTokens: models.reduce((n, e) => n + (e.input_tokens ?? 0), 0),
    outputTokens: models.reduce((n, e) => n + (e.output_tokens ?? 0), 0),
    toolCalls: events.filter((e) => e.kind === "tool").length,
    models: [...new Set(models.map((e) => e.model).filter((m): m is string => !!m))],
    fallback: models.some((e) => e.fallback),
    retries: events.filter((e) => e.kind === "verify" && e.result === "retry").length,
  };
}
