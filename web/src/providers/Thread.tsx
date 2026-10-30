import { validate } from "uuid";
import { getApiKey } from "@/lib/api-key";
import { resolveApiUrl } from "@/lib/resolve-api-url";
import { Thread } from "@langchain/langgraph-sdk";
import { useQueryState } from "nuqs";
import {
  createContext,
  useContext,
  ReactNode,
  useCallback,
  useState,
  useEffect,
  useRef,
  Dispatch,
  SetStateAction,
} from "react";
import { createClient } from "./client";

interface ThreadContextType {
  getThreads: () => Promise<Thread[]>;
  threads: Thread[];
  setThreads: Dispatch<SetStateAction<Thread[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
  refreshThreads: () => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
  threadsError: string | null;
}

const ThreadContext = createContext<ThreadContextType | undefined>(undefined);

function getThreadSearchMetadata(
  assistantId: string,
): { graph_id: string } | { assistant_id: string } {
  if (validate(assistantId)) {
    return { assistant_id: assistantId };
  } else {
    return { graph_id: assistantId };
  }
}

export function ThreadProvider({ children }: { children: ReactNode }) {
  const envApiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID;
  const envAuthScheme: string | undefined = process.env.NEXT_PUBLIC_AUTH_SCHEME;

  const [apiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || "",
  });
  const [assistantId] = useQueryState("assistantId");
  const [authScheme] = useQueryState("authScheme", {
    defaultValue: envAuthScheme || "",
  });
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const [threadsError, setThreadsError] = useState<string | null>(null);
  const generation = useRef(0);

  const getThreads = useCallback(async (): Promise<Thread[]> => {
    const finalApiUrl = resolveApiUrl(apiUrl, envApiUrl);
    const resolvedAssistantId = assistantId || envAssistantId;
    if (!finalApiUrl || !resolvedAssistantId) return [];
    const client = createClient(
      finalApiUrl,
      getApiKey(finalApiUrl) ?? undefined,
      authScheme || undefined,
    );

    const threads = await client.threads.search({
      metadata: {
        ...getThreadSearchMetadata(resolvedAssistantId),
      },
      limit: 100,
    });

    return threads;
  }, [apiUrl, envApiUrl, assistantId, authScheme, envAssistantId]);

  const refreshThreads = useCallback(async () => {
    const request = ++generation.current;
    setThreadsLoading(true);
    setThreadsError(null);
    try {
      const result = await getThreads();
      if (request === generation.current) setThreads(result);
    } catch {
      if (request === generation.current)
        setThreadsError("对话列表加载失败，请确认后端服务已启动后重试。");
    } finally {
      if (request === generation.current) setThreadsLoading(false);
    }
  }, [getThreads]);

  useEffect(() => {
    setThreads([]);
    void refreshThreads();
    return () => {
      generation.current++;
    };
  }, [refreshThreads]);

  const deleteThread = useCallback(
    async (id: string) => {
      const url = resolveApiUrl(apiUrl, envApiUrl);
      const client = createClient(
        url,
        getApiKey(url) ?? undefined,
        authScheme || undefined,
      );
      await client.threads.delete(id);
      // 使删除前发出的列表请求过期，防止已删除对话重新出现在列表中。
      generation.current++;
      setThreadsLoading(false);
      setThreads((items) => items.filter((t) => t.thread_id !== id));
    },
    [apiUrl, envApiUrl, authScheme],
  );

  const value = {
    getThreads,
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
    refreshThreads,
    deleteThread,
    threadsError,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

export function useThreads() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreads must be used within a ThreadProvider");
  }
  return context;
}
