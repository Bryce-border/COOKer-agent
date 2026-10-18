"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { Provider, PROVIDERS } from "@/lib/cooker";

const values = PROVIDERS.map((p) => p.value);

/** 当前模型厂商，存在 URL 查询参数里（刷新不丢失），随每次运行作为 context.provider 传给后端。 */
export function useProvider() {
  return useQueryState(
    "provider",
    parseAsStringLiteral(values).withDefault("qwen" as Provider),
  );
}

export function ProviderSelect({ disabled }: { disabled?: boolean }) {
  const [provider, setProvider] = useProvider();
  return (
    <select
      aria-label="模型"
      data-testid="provider-select"
      className="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
      value={provider}
      disabled={disabled}
      onChange={(e) => setProvider(e.target.value as Provider)}
    >
      {PROVIDERS.map((p) => (
        <option
          key={p.value}
          value={p.value}
        >
          {p.label}
        </option>
      ))}
    </select>
  );
}
