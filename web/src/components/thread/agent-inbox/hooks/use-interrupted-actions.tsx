import { useStreamContext } from "@/providers/Stream";
import { END } from "@langchain/langgraph/web";
import { Interrupt } from "@langchain/langgraph-sdk";
import { toast } from "sonner";
import {
  Dispatch,
  KeyboardEvent,
  MutableRefObject,
  SetStateAction,
  useEffect,
  useRef,
  useState,
} from "react";
import { Decision, DecisionWithEdits, HITLRequest, SubmitType } from "../types";
import { buildDecisionFromState, createDefaultHumanResponse } from "../utils";

interface UseInterruptedActionsInput {
  interrupt: Interrupt<HITLRequest>;
}

interface UseInterruptedActionsValue {
  handleSubmit: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => Promise<void>;
  handleResolve: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => Promise<void>;
  streaming: boolean;
  streamFinished: boolean;
  loading: boolean;
  supportsMultipleMethods: boolean;
  hasEdited: boolean;
  hasAddedResponse: boolean;
  approveAllowed: boolean;
  humanResponse: DecisionWithEdits[];
  selectedSubmitType: SubmitType | undefined;
  setSelectedSubmitType: Dispatch<SetStateAction<SubmitType | undefined>>;
  setHumanResponse: Dispatch<SetStateAction<DecisionWithEdits[]>>;
  setHasAddedResponse: Dispatch<SetStateAction<boolean>>;
  setHasEdited: Dispatch<SetStateAction<boolean>>;
  initialHumanInterruptEditValue: MutableRefObject<Record<string, string>>;
}

export default function useInterruptedActions({
  interrupt,
}: UseInterruptedActionsInput): UseInterruptedActionsValue {
  const thread = useStreamContext();
  const [humanResponse, setHumanResponse] = useState<DecisionWithEdits[]>([]);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamFinished, setStreamFinished] = useState(false);
  const [selectedSubmitType, setSelectedSubmitType] = useState<SubmitType>();
  const [hasEdited, setHasEdited] = useState(false);
  const [hasAddedResponse, setHasAddedResponse] = useState(false);
  const [approveAllowed, setApproveAllowed] = useState(false);
  const initialHumanInterruptEditValue = useRef<Record<string, string>>({});

  useEffect(() => {
    const hitlValue = interrupt.value as HITLRequest | undefined;
    initialHumanInterruptEditValue.current = {};

    if (!hitlValue) {
      setHumanResponse([]);
      setSelectedSubmitType(undefined);
      setApproveAllowed(false);
      setHasEdited(false);
      setHasAddedResponse(false);
      return;
    }

    try {
      const { responses, defaultSubmitType, hasApprove } =
        createDefaultHumanResponse(hitlValue, initialHumanInterruptEditValue);
      setHumanResponse(responses);
      setSelectedSubmitType(defaultSubmitType);
      setApproveAllowed(hasApprove);
      setHasEdited(false);
      setHasAddedResponse(false);
    } catch (error) {
      console.error("Error formatting and setting human response state", error);
      setHumanResponse([]);
      setSelectedSubmitType(undefined);
      setApproveAllowed(false);
    }
  }, [interrupt]);

  const resumeRun = (decisions: Decision[]): boolean => {
    try {
      thread.submit(
        {},
        {
          command: {
            resume: {
              decisions,
            },
          },
        },
      );
      return true;
    } catch (error) {
      console.error("Error sending human response", error);
      return false;
    }
  };

  const handleSubmit = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => {
    e.preventDefault();
    const { decision, error } = buildDecisionFromState(
      humanResponse,
      selectedSubmitType,
    );

    if (!decision) {
      toast.error("操作失败", {
        description: error ?? "不支持此回复类型。",
        duration: 5000,
        richColors: true,
        closeButton: true,
      });
      return;
    }

    if (error) {
      toast.error("操作失败", {
        description: error,
        duration: 5000,
        richColors: true,
        closeButton: true,
      });
      return;
    }

    let errorOccurred = false;
    initialHumanInterruptEditValue.current = {};

    try {
      setLoading(true);
      setStreaming(true);

      const resumedSuccessfully = resumeRun([decision]);
      if (!resumedSuccessfully) {
        errorOccurred = true;
        return;
      }

      toast("操作成功", {
        description: "回复已提交。",
        duration: 5000,
      });

      setStreamFinished(true);
    } catch (error: any) {
      console.error("Error sending human response", error);
      errorOccurred = true;

      if ("message" in error && error.message.includes("Invalid assistant")) {
        toast("助手标识无效", {
          description:
            "未找到指定的助手，请修改连接设置中的助手标识后重试。",
          richColors: true,
          closeButton: true,
          duration: 5000,
        });
      } else {
        toast.error("操作失败", {
          description: "回复提交失败。",
          richColors: true,
          closeButton: true,
          duration: 5000,
        });
      }
    } finally {
      setStreaming(false);
      setLoading(false);
      if (errorOccurred) {
        setStreamFinished(false);
      }
    }
  };

  const handleResolve = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => {
    e.preventDefault();
    setLoading(true);
    initialHumanInterruptEditValue.current = {};

    try {
      thread.submit(
        {},
        {
          command: {
            goto: END,
          },
        },
      );

      toast("操作成功", {
        description: "已将对话标记为已处理。",
        duration: 3000,
      });
    } catch (error) {
      console.error("Error marking thread as resolved", error);
      toast.error("操作失败", {
        description: "未能将对话标记为已处理。",
        richColors: true,
        closeButton: true,
        duration: 3000,
      });
    } finally {
      setLoading(false);
    }
  };

  const supportsMultipleMethods =
    humanResponse.filter((response) =>
      ["edit", "approve", "reject"].includes(response.type),
    ).length > 1;

  return {
    handleSubmit,
    handleResolve,
    humanResponse,
    selectedSubmitType,
    streaming,
    streamFinished,
    loading,
    supportsMultipleMethods,
    hasEdited,
    hasAddedResponse,
    approveAllowed,
    setSelectedSubmitType,
    setHumanResponse,
    setHasAddedResponse,
    setHasEdited,
    initialHumanInterruptEditValue,
  };
}
