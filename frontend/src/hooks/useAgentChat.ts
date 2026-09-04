import { useCallback, useRef, useState } from "react";
import { agentChat } from "../api/agent";
import type {
  AgentStepEntry,
  ChatMessage,
  ChatSourceEntry,
  PendingConfirmation,
} from "../api/types";

/** Решение по запросу подтверждения деструктивного инструмента (пункт 9). */
export interface ConfirmationParams {
  decision: "approve" | "reject";
  pending: PendingConfirmation;
}

interface UseAgentChatResult {
  /** Прогон выполняется */
  isRunning: boolean;
  /** Ошибка последнего прогона (доменная или лимит) */
  error: { code: string; message: string } | null;
  /** Финальный ответ ассистента */
  content: string;
  /** Шаги последнего прогона */
  steps: AgentStepEntry[];
  /** Источники поиска последнего прогона */
  sources: ChatSourceEntry[] | null;
  /** Причина недоступности, если дополнение не установлено */
  unavailableReason: string | null;
  /** Идентификатор разговора, созданный/использованный сервером */
  conversationId: string | null;
  /** Запрос подтверждения деструктивного действия, если прогон остановлен */
  pendingConfirmation: PendingConfirmation | null;
  /** Запустить прогон */
  send: (params: {
    messages: ChatMessage[];
    modelAlias: string;
    conversationId?: string | null;
    corpusNames?: string[] | null;
    confirmation?: ConfirmationParams | null;
    onDone?: (content: string, error: { code: string; message: string } | null) => void;
  }) => void;
  /** Прервать прогон */
  abort: () => void;
}

/**
 * Агентный прогон (Т-502). В отличие от чата — один синхронный запрос,
 * ответ целиком; стриминга нет. Хранит шаги, источники и идентификатор
 * разговора, чтобы последующие сообщения продолжали тот же диалог.
 * Цикл подтверждения (пункт 9): если ответ содержит запрос
 * подтверждения, клиент показывает карточку решения и отправляет его
 * следующим запросом вместе с тем же буфером сообщений.
 */
export function useAgentChat(): UseAgentChatResult {
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [content, setContent] = useState("");
  const [steps, setSteps] = useState<AgentStepEntry[]>([]);
  const [sources, setSources] = useState<ChatSourceEntry[] | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingConfirmation | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsRunning(false);
  }, []);

  const send = useCallback<UseAgentChatResult["send"]>(
    ({ messages, modelAlias, conversationId: convId, corpusNames, confirmation, onDone }) => {
      setError(null);
      setContent("");
      setSteps([]);
      setSources(null);
      setUnavailableReason(null);
      setIsRunning(true);

      const controller = new AbortController();
      abortRef.current = controller;

      (async () => {
        try {
          const result = await agentChat(
            {
              messages,
              model_alias: modelAlias,
              conversation_id: convId ?? null,
              corpus_names: corpusNames && corpusNames.length > 0 ? corpusNames : null,
              confirmation_decision: confirmation?.decision ?? null,
              confirmation: confirmation?.pending ?? null,
            },
            controller.signal,
          );

          if (!result.available) {
            setUnavailableReason(result.reason ?? "Агентный модуль недоступен");
            onDone?.("", null);
            return;
          }
          if (result.type === "error") {
            const msg = result.hint ?? "Агентный прогон остановлен";
            setError({ code: result.code ?? "agent_error", message: msg });
            setConversationId(result.conversation_id ?? null);
            setPendingConfirmation(null);
            onDone?.("", { code: result.code ?? "agent_error", message: msg });
            return;
          }
          setContent(result.content);
          setSteps(result.steps);
          setSources(result.sources);
          setConversationId(result.conversation_id ?? null);
          setPendingConfirmation(result.pending_confirmation ?? null);
          onDone?.(result.content, null);
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            onDone?.("", null);
          } else {
            const apiErr = err as { error?: string; reason?: string };
            const msg =
              apiErr?.reason ?? (err instanceof Error ? err.message : "Неизвестная ошибка");
            setError({ code: apiErr?.error ?? "agent_error", message: msg });
            onDone?.("", { code: apiErr?.error ?? "agent_error", message: msg });
          }
        } finally {
          setIsRunning(false);
          abortRef.current = null;
        }
      })();
    },
    [],
  );

  return {
    isRunning,
    error,
    content,
    steps,
    sources,
    unavailableReason,
    conversationId,
    pendingConfirmation,
    send,
    abort,
  };
}
