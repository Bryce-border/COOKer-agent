import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import type { Client, ThreadState } from "@langchain/langgraph-sdk";
import type { StateType } from "@/providers/Stream";

/** 使用 SDK 的受控历史接口，捕获初始化请求失败并隔离旧请求的结果。 */
export function useConversationHistory(
  client: Client,
  threadId: string | null,
  createdThreadRef: MutableRefObject<string | null>,
) {
  const generation = useRef(0);
  const [snapshot, setSnapshot] = useState<{
    id: string | null;
    data?: ThreadState<StateType>[];
    error?: unknown;
    loading: boolean;
  }>({ id: null, loading: false });
  const mutate = useCallback(
    async (id = threadId ?? undefined) => {
      const request = ++generation.current;
      if (!id) {
        setSnapshot({ id: null, loading: false });
        return [];
      }
      setSnapshot((old) => ({
        id,
        data: old.id === id ? old.data : undefined,
        loading: true,
      }));
      try {
        const data = await client.threads.getHistory<StateType>(id, {
          limit: 1,
        });
        if (request === generation.current)
          setSnapshot({ id, data, loading: false });
        return data;
      } catch (error) {
        if (request === generation.current)
          setSnapshot({ id, error, loading: false });
        // 失败保存在受控状态；不把被忽略的拒绝 Promise 交给开发环境报错浮层。
        return [];
      }
    },
    [client, threadId],
  );
  useEffect(() => {
    // 刚创建的流尚未写入检查点，等待 SDK 在流结束时调用 mutate(id)。
    if (threadId && createdThreadRef.current === threadId) {
      createdThreadRef.current = null;
      setSnapshot({ id: threadId, loading: false });
      return;
    }
    void mutate();
    return () => {
      generation.current++;
    };
  }, [threadId, mutate, createdThreadRef]);
  const matches = snapshot.id === threadId;
  return {
    data: matches ? snapshot.data : undefined,
    error: matches ? snapshot.error : undefined,
    isLoading: matches ? snapshot.loading : !!threadId,
    mutate,
  };
}
