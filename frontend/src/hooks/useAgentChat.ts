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
  /** Инструменты выбранного скилла, недоступные в этом прогоне (Т-508) */
  skillToolsUnavailable: string[];
  /** Прогон завершён остановкой по запросу пользователя (Т-509, решение 7) */
  stopped: boolean;
  /** Запустить прогон */
  send: (params: {
    messages: ChatMessage[];
    /** Алиас модели для ad-hoc прогона; при профиле — null (модель фиксирует профиль) */
    modelAlias: string | null;
    /** Профиль агента (Т-509): фиксирует модель и скилл диалога */
    agentProfileId?: string | null;
    conversationId?: string | null;
    corpusNames?: string[] | null;
    skillId?: string | null;
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
 * Скилл (Т-508): выбор приходит на каждый запрос (``skillId``), а не
 * хранится на диалоге. Профиль агента (Т-509) задаёт модель и скилл на
 * сервере — при выбранном профиле клиент не отправляет ни ``modelAlias``,
 * ни ``skillId``: переопределение конфигурации профиля — явный отказ 400,
 * а не молчаливое игнорирование полей.
 * Остановка (Т-509, решение 7): запрос ``POST /api/conversations/{id}/stop``
 * ставит на сервере флаг, который цикл проверяет между шагами, поэтому
 * ответ приходит с ``type="stopped"`` — прогон завершён штатно, расход
 * выполненных шагов сохранён. Отличается от ``abort()``: тот обрывает fetch
 * на клиенте, не доходя до сервера.
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
  const [skillToolsUnavailable, setSkillToolsUnavailable] = useState<string[]>([]);
  const [stopped, setStopped] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsRunning(false);
  }, []);

  const send = useCallback<UseAgentChatResult["send"]>(
    ({
      messages,
      modelAlias,
      agentProfileId,
      conversationId: convId,
      corpusNames,
      skillId,
      confirmation,
      onDone,
    }) => {
      setError(null);
      setContent("");
      setSteps([]);
      setSources(null);
      setUnavailableReason(null);
      setSkillToolsUnavailable([]);
      setStopped(false);
      setIsRunning(true);

      const controller = new AbortController();
      abortRef.current = controller;

      (async () => {
        try {
          const result = await agentChat(
            {
              messages,
              // Профиль фиксирует модель и скилл (решение 2): при выбранном
              // профиле эти поля не отправляются вовсе — иначе сервер
              // отвечает 400 agent_profile_conflict.
              model_alias: agentProfileId ? null : modelAlias,
              agent_profile_id: agentProfileId ?? null,
              conversation_id: convId ?? null,
              corpus_names: corpusNames && corpusNames.length > 0 ? corpusNames : null,
              skill_id: agentProfileId ? null : (skillId ?? null),
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
          setSkillToolsUnavailable(result.skill_tools_unavailable ?? []);
          // Остановка по запросу (решение 7) — штатное завершение прогона,
          // не ошибка: ответ и шаги сохраняются, лента показывает, чем
          // прогон закончился.
          setStopped(result.type === "stopped");
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
    skillToolsUnavailable,
    stopped,
    send,
    abort,
  };
}
