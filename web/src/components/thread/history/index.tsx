import { Button } from "@/components/ui/button";
import { useThreads } from "@/providers/Thread";
import { Thread } from "@langchain/langgraph-sdk";
import { useState } from "react";
import { useStreamContext } from "@/providers/Stream";
import { toast } from "sonner";

import { getContentString } from "../utils";
import { useQueryState, parseAsBoolean } from "nuqs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { PanelRightOpen, PanelRightClose, Trash2 } from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";

function ThreadList({
  threads,
  onThreadClick,
}: {
  threads: Thread[];
  onThreadClick?: (threadId: string) => void;
}) {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { deleteThread } = useThreads();
  const stream = useStreamContext();
  const [deleting, setDeleting] = useState<string | null>(null);

  const remove = async (id: string) => {
    if (
      !window.confirm(
        "确定永久删除这段对话吗？删除后无法恢复，长期偏好不受影响。",
      )
    )
      return;
    setDeleting(id);
    try {
      if (id === threadId && stream.isLoading) await stream.stop();
      await deleteThread(id);
      if (id === threadId) await setThreadId(null);
      toast.success("对话已删除");
    } catch {
      toast.error("删除失败，对话已保留，请稍后重试。");
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {threads.map((t) => {
        let itemText = t.thread_id;
        if (
          typeof t.values === "object" &&
          t.values &&
          "messages" in t.values &&
          Array.isArray(t.values.messages) &&
          t.values.messages?.length > 0
        ) {
          const firstMessage = t.values.messages[0];
          itemText = getContentString(firstMessage.content);
        }
        return (
          <div
            key={t.thread_id}
            className="flex w-full min-w-0 items-center px-1"
          >
            <Button
              variant="ghost"
              className="min-w-0 flex-1 items-start justify-start text-left font-normal"
              aria-current={t.thread_id === threadId ? "page" : undefined}
              disabled={!!deleting || stream.isLoading}
              onClick={(e) => {
                e.preventDefault();
                onThreadClick?.(t.thread_id);
                if (t.thread_id === threadId) return;
                setThreadId(t.thread_id);
              }}
            >
              <p className="truncate text-ellipsis">{itemText}</p>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`删除对话：${itemText}`}
              disabled={!!deleting}
              onClick={() => void remove(t.thread_id)}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function ThreadHistoryLoading() {
  return (
    <div className="flex h-full w-full flex-col items-start justify-start gap-2 overflow-y-scroll [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-track]:bg-transparent">
      {Array.from({ length: 30 }).map((_, i) => (
        <Skeleton
          key={`skeleton-${i}`}
          className="h-10 w-[280px]"
        />
      ))}
    </div>
  );
}

export default function ThreadHistory() {
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );

  const { refreshThreads, threads, threadsLoading, threadsError } =
    useThreads();

  const list = threadsLoading ? (
    <ThreadHistoryLoading />
  ) : threadsError ? (
    <div
      role="alert"
      className="space-y-3 p-4 text-sm"
    >
      <p>{threadsError}</p>
      <Button
        variant="outline"
        onClick={() => void refreshThreads()}
      >
        重试加载
      </Button>
    </div>
  ) : threads.length ? (
    <ThreadList
      threads={threads}
      onThreadClick={() => {
        if (!isLargeScreen) void setChatHistoryOpen(false);
      }}
    />
  ) : (
    <p className="p-4 text-sm text-gray-500">还没有对话，发条消息开始吧。</p>
  );

  return (
    <>
      <div className="shadow-inner-right hidden h-screen w-[300px] shrink-0 flex-col items-start justify-start gap-6 border-r-[1px] border-slate-300 lg:flex">
        <div className="flex w-full items-center justify-between px-4 pt-1.5">
          <Button
            className="hover:bg-gray-100"
            variant="ghost"
            onClick={() => setChatHistoryOpen((p) => !p)}
          >
            {chatHistoryOpen ? (
              <PanelRightOpen className="size-5" />
            ) : (
              <PanelRightClose className="size-5" />
            )}
          </Button>
          <h1 className="text-xl font-semibold tracking-tight">历史对话</h1>
        </div>
        {list}
      </div>
      <div className="lg:hidden">
        <Sheet
          open={!!chatHistoryOpen && !isLargeScreen}
          onOpenChange={(open) => {
            if (isLargeScreen) return;
            setChatHistoryOpen(open);
          }}
        >
          <SheetContent
            side="left"
            className="flex lg:hidden"
          >
            <SheetHeader>
              <SheetTitle>历史对话</SheetTitle>
            </SheetHeader>
            {list}
          </SheetContent>
        </Sheet>
      </div>
    </>
  );
}
