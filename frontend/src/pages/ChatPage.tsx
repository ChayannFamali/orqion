import { useCallback, useEffect, useState } from "react";
import { Bot, Download, Eraser, Square } from "lucide-react";
import {
  useConversations,
  useConversation,
  useUpdateConversation,
  useResetConversationContext,
  useDeleteConversation,
  useStopConversation,
} from "../hooks/useConversations";
import { useEnabledModels } from "../hooks/useModels";
import { useAvailableCorpora } from "../hooks/useCorpora";
import { useChat } from "../hooks/useChat";
import { useAgentChat } from "../hooks/useAgentChat";
import { useCurrentUser } from "../hooks/useAuth";
import { usePromptTemplates } from "../hooks/usePromptTemplates";
import { useAvailableSkills } from "../hooks/useSkills";
import { useAvailableAgentProfiles } from "../hooks/useAgentProfiles";
import { ConversationList } from "../components/ConversationList";
import { ChatMessages } from "../components/ChatMessages";
import { ChatInput } from "../components/ChatInput";
import { ModelSelector } from "../components/ModelSelector";
import { CorpusSelector } from "../components/CorpusSelector";
import { NewChatModal } from "../components/NewChatModal";
import { AgentRunSummary } from "../components/AgentRunSummary";
import type { ChatMessage, MessageResponse } from "../api/types";
import { conversationToMarkdown, downloadMarkdown, sanitizeFilename } from "../utils/exportConversation";
import { estimateTokens } from "../utils/estimateTokens";

/** Запрос старта агентного диалога из раздела «Агенты» (Т-509). */
export interface AgentStartRequest {
  /** Идентификатор профиля; ``null`` — ad-hoc диалог с ручным выбором модели */
  profileId: string | null;
  /** Счётчик: повторный клик по тому же профилю обязан сработать снова */
  nonce: number;
}

interface ChatPageProps {
  agentStart?: AgentStartRequest | null;
}

export function ChatPage({ agentStart }: ChatPageProps = {}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [newChatOpen, setNewChatOpen] = useState(false);
  // T-439: имена выбранных корпусов для RAG-запроса (мульти-режим)
  const [selectedCorpora, setSelectedCorpora] = useState<string[]>([]);
  // Т-445 (каркас, Г1): режим рассуждения на уровне сообщения. Переключатель
  // виден только при политике "optional"; выбор не запоминается на диалог —
  // сбрасывается на дефолт (выключен = "авто") при новом диалоге.
  const [reasoningOn, setReasoningOn] = useState(false);
  // Т-502 (решение 10): агентный режим — отдельная точка создания, обычный
  // чат поведение не меняет. Режим следует за разговором (поле ``mode``).
  const [agentMode, setAgentMode] = useState(false);
  const [agentConvId, setAgentConvId] = useState<string | null>(null);
  const [newAgentChatOpen, setNewAgentChatOpen] = useState(false);
  // Т-508: выбранный скилл. Выбор приходит на каждый запрос, а не хранится
  // на диалоге (решение 5).
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null);
  // Т-509 (решение 2): профиль агента фиксирует модель и скилл диалога,
  // поэтому при выбранном профиле селекторы модели и скилла скрыты и их
  // значения не отправляются. Пустой — ad-hoc диалог с ручным выбором.
  const [agentProfileId, setAgentProfileId] = useState<string | null>(null);

  const currentUser = useCurrentUser();
  const reasoningPolicy = currentUser.data?.reasoning ?? "off";
  const reasoningOptional = reasoningPolicy === "optional";

  // Т-507: личные шаблоны промптов — выбор у поля ввода. Запрос идёт
  // только при наличии способности; без неё списка не существует (404).
  const capabilities = currentUser.data?.capabilities ?? [];
  const canPrompts =
    capabilities.includes("*") || capabilities.includes("custom_prompts");
  const promptTemplates = usePromptTemplates(canPrompts);

  const conversations = useConversations();
  const conversation = useConversation(activeId);
  const models = useEnabledModels();
  const availableCorpora = useAvailableCorpora();
  const updateConv = useUpdateConversation();
  const resetContext = useResetConversationContext();
  const deleteConv = useDeleteConversation();
  // Т-509 (решение 7): запрос остановки прогона. Мутация только ставит флаг
  // на сервере — завершает прогон сам цикл между шагами.
  const stopConversation = useStopConversation();
  const chat = useChat();
  const agent = useAgentChat();
  // Т-508: список скиллов для выбора — только в агентном режиме.
  const availableSkills = useAvailableSkills(agentMode);
  // Т-509: имя профиля для подписи в шапке. Запрос только при активном
  // профиле: список выбора доступен всем аутентифицированным, поэтому 404
  // здесь невозможен.
  const availableProfiles = useAvailableAgentProfiles(
    agentMode && agentProfileId !== null,
  );
  const activeProfile = availableProfiles.data?.profiles.find(
    (p) => p.id === agentProfileId,
  );

  // Т-502: модели с флагом пригодности к инструментам (решение 3). Точка
  // создания агентного диалога видна только при наличии хотя бы одной.
  const agentModels = (models.data ?? []).filter((m) => m.supports_tools);
  const hasAgentModels = agentModels.length > 0;

  // Явный дефолт вместо null: селектор всегда показывает модель, которая
  // ответит; запрос уходит с model_alias, а не с неявным candidates[0]
  useEffect(() => {
    if (selectedModel === null && models.data && models.data.length > 0) {
      setSelectedModel(models.data[0].alias);
    }
  }, [models.data, selectedModel]);

  // Т-445: выбор рассуждения не запоминается на диалог — при смене диалога
  // сброс на дефолт политики (для "optional" это "авто" = выключено).
  useEffect(() => {
    setReasoningOn(false);
  }, [activeId]);

  // Т-502: режим следует за загруженным разговором (поле ``mode``). Для
  // нового (ещё не сохранённого) диалога режим задаёт точка создания.
  // Т-509 (решение 2): профиль тоже следует за разговором — модель и скилл
  // зафиксированы им, поэтому селекторы для профильного диалога скрыты.
  useEffect(() => {
    if (activeId && conversation.data) {
      const isAgent = conversation.data.mode === "agent";
      setAgentMode(isAgent);
      if (isAgent) {
        setAgentConvId(activeId);
        setAgentProfileId(conversation.data.agent_profile_id ?? null);
      } else {
        setAgentProfileId(null);
      }
    }
  }, [activeId, conversation.data]);

  // Т-509: старт диалога из раздела «Агенты». С профилем — сразу агентный
  // диалог с пустым буфером; без профиля (ad-hoc) — существующая модалка
  // выбора модели: профили этот путь не отбирают (решение 9).
  useEffect(() => {
    if (!agentStart) return;
    if (agentStart.profileId === null) {
      setNewAgentChatOpen(true);
      return;
    }
    setActiveId(null);
    setLocalMessages([]);
    setAgentConvId(null);
    setAgentMode(true);
    setAgentProfileId(agentStart.profileId);
    setSelectedSkillId(null);
  }, [agentStart]);

  // Т-502: сервер создаёт разговор при первом прогоне — фиксируем его
  // идентификатор, чтобы следующие сообщения продолжали тот же диалог.
  useEffect(() => {
    if (agentMode && agent.conversationId) {
      setAgentConvId(agent.conversationId);
    }
  }, [agentMode, agent.conversationId]);

  // Эффективный выбор на уровне сообщения: учитывается только при "optional".
  const reasoningMode = reasoningOptional && reasoningOn ? "on" : null;

  const displayedMessages: MessageResponse[] = conversation.data?.messages ?? [];

  // T-442: занятость окна контекста = оценка токенов буфера отправки
  // (именно он уходит в модель). После сброса буфер обнуляется → 0.
  const selectedModelMeta = models.data?.find((m) => m.alias === selectedModel);
  const contextUsage = {
    used: localMessages.reduce((acc, m) => acc + estimateTokens(m.content), 0),
    max: selectedModelMeta?.max_input_tokens ?? null,
  };

  // Т-502: в агентном режиме — только модели с флагом, занятость от прогона.
  const selectorModels = agentMode ? agentModels : (models.data ?? []);
  const isBusy = agentMode ? agent.isRunning : chat.isStreaming;
  // Т-509 (решение 2): для профильного диалога модель задаёт профиль, а не
  // селектор, поэтому гейт «нет пригодных моделей в списке выбора» к нему не
  // относится. Иначе интерфейс блокировал бы прогон, который сервер принял
  // бы: модель профиля пригодна к инструментам по проверке на создании, а в
  // список выбора пользователя попадает по политике — это разные множества.
  const modelReady = agentProfileId !== null || selectorModels.length > 0;

  const handleSelect = useCallback((id: string) => {
    setActiveId(id);
    setLocalMessages([]);
    setAgentConvId(null);
    // Профиль подтянется из загруженного диалога (эффект выше); до загрузки
    // селекторы показывать нельзя — иначе выбор модели ушёл бы в запрос
    // профильного диалога и получил 400.
    setAgentProfileId(null);
  }, []);

  // T-439: переключение корпуса в мульти-селекторе
  const handleToggleCorpus = useCallback((name: string) => {
    setSelectedCorpora((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  }, []);

  const handleNew = useCallback(() => {
    setNewChatOpen(true);
  }, []);

  const handleCreateChat = useCallback((alias: string) => {
    setActiveId(null);
    setLocalMessages([]);
    setSelectedModel(alias);
    setAgentMode(false);
    setAgentConvId(null);
    setAgentProfileId(null);
    setNewChatOpen(false);
  }, []);

  // Т-502 (решение 10): отдельная точка создания агентного диалога.
  const handleNewAgent = useCallback(() => {
    setNewAgentChatOpen(true);
  }, []);

  // Т-509 (решение 9): ad-hoc агентный диалог — ручной выбор модели, без
  // профиля. Профили этот путь не отбирают.
  const handleCreateAgent = useCallback((alias: string) => {
    setActiveId(null);
    setLocalMessages([]);
    setSelectedModel(alias);
    setAgentMode(true);
    setAgentConvId(null);
    setAgentProfileId(null);
    setNewAgentChatOpen(false);
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      const userMsg: ChatMessage = { role: "user", content: text };
      const assistantMsg: ChatMessage = { role: "assistant", content: "" };

      const messagesToSend = [...localMessages, userMsg];
      setLocalMessages([...messagesToSend, assistantMsg]);

      // Т-502: агентный прогон — синхронный цикл, отдельный эндпоинт.
      // Т-509 (решение 2): при выбранном профиле модель и скилл задаёт
      // сервер — в запрос уходит идентификатор профиля, а значения
      // селекторов хук не отправляет (переопределение дало бы 400).
      if (agentMode && (agentProfileId || selectedModel)) {
        agent.send({
          messages: messagesToSend,
          modelAlias: selectedModel,
          agentProfileId,
          conversationId: agentConvId,
          corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
          skillId: selectedSkillId,
          onDone: (fullContent) => {
            const updated = [...messagesToSend, { role: "assistant", content: fullContent }];
            setLocalMessages(updated);
            conversations.refetch();
            if (activeId !== null) {
              conversation.refetch();
            }
          },
        });
        return;
      }

      chat.sendMessage({
        messages: messagesToSend,
        modelAlias: selectedModel,
        conversationId: activeId,
        corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
        reasoningMode,
        onDone: (fullContent) => {
          const updated = [...messagesToSend, { role: "assistant", content: fullContent }];
          setLocalMessages(updated);
          if (activeId === null) {
            conversations.refetch();
          } else {
            conversation.refetch();
          }
        },
      });
    },
    [localMessages, selectedModel, activeId, selectedCorpora, reasoningMode, agentMode, agentConvId, selectedSkillId, agentProfileId, agent, chat, conversations, conversation],
  );

  const handleAbort = useCallback(() => {
    chat.abort();
    agent.abort();
  }, [chat, agent]);

  const handleExport = useCallback(() => {
    if (!conversation.data) return;
    const markdown = conversationToMarkdown(conversation.data, window.location.origin);
    const filename = sanitizeFilename(conversation.data.title);
    downloadMarkdown(filename, markdown);
  }, [conversation.data]);

  // T-442: мягкий сброс контекста — маркер на сервере, буфер отправки
  // обнуляется (в модель уйдут только сообщения после маркера), видимая
  // лента сохраняется. RAG-привязка и бюджет не затрагиваются.
  const handleResetContext = useCallback(() => {
    if (!activeId) return;
    resetContext.mutate(activeId, {
      onSuccess: () => setLocalMessages([]),
    });
  }, [activeId, resetContext]);

  // T-443: удаление диалога из списка. Если удалён активный —
  // переключение на следующий (последний по времени) или пустое состояние.
  const handleDeleteConversation = useCallback(
    (id: string) => {
      deleteConv.mutate(id, {
        onSuccess: () => {
          if (activeId === id) {
            const remaining = (conversations.data?.conversations ?? []).filter(
              (c) => c.id !== id,
            );
            setActiveId(remaining.length > 0 ? remaining[0].id : null);
            setLocalMessages([]);
          }
        },
      });
    },
    [activeId, conversations.data, deleteConv],
  );

  const handleRegenerate = useCallback(() => {
    // Убираем последний ответ ассистента, отправляем заново
    const msgs = [...localMessages];
    // Найти последний assistant и обрезать
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant") {
        msgs.splice(i);
        break;
      }
    }
    setLocalMessages(msgs);

    const messagesToSend = msgs.filter((m) => m.role === "user" || m.content);
    chat.sendMessage({
      messages: messagesToSend,
      modelAlias: selectedModel,
      conversationId: activeId,
      corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
      reasoningMode,
      onDone: (fullContent) => {
        const updated = [...messagesToSend, { role: "assistant", content: fullContent }];
        setLocalMessages(updated);
        if (activeId === null) {
          conversations.refetch();
        } else {
          conversation.refetch();
        }
      },
    });
  }, [localMessages, selectedModel, activeId, selectedCorpora, reasoningMode, chat, conversations, conversation]);

  // Пункт 9 ревью Т-502: деструктивный инструмент останавливает прогон
  // до выполнения и запрашивает подтверждение. Решение уходит следующим
  // запросом вместе с тем же буфером сообщений — без нового сообщения
  // пользователя.
  //
  // Выбор корпусов и скилла передаётся тот же, что в исходном запросе:
  // сужение и класс данных вычисляются на каждый запрос заново, поэтому
  // без них продолжение одобренного прогона шло бы с полным реестром
  // инструментов и с классом данных «не выбран» — ослабление обеих
  // гарантий (Т-508, решения 2 и 5).
  const handleAgentConfirmation = useCallback(
    (decision: "approve" | "reject") => {
      const pending = agent.pendingConfirmation;
      if (!pending || !selectedModel) return;
      const messagesToSend = localMessages.filter((m) => m.role === "user" || m.content);
      agent.send({
        messages: messagesToSend,
        modelAlias: selectedModel,
        conversationId: agentConvId,
        corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
        skillId: selectedSkillId,
        confirmation: { decision, pending },
        onDone: (fullContent) => {
          const updated = [...messagesToSend, { role: "assistant", content: fullContent }];
          setLocalMessages(updated);
          conversations.refetch();
          if (activeId !== null) {
            conversation.refetch();
          }
        },
      });
    },
    [
      agent,
      selectedModel,
      localMessages,
      agentConvId,
      selectedCorpora,
      selectedSkillId,
      conversations,
      conversation,
      activeId,
    ],
  );

  const handleEdit = useCallback(
    (messageIndex: number, newContent: string) => {
      // Обрезаем всё после отредактированного сообщения, заменяем контент
      const msgs = localMessages.slice(0, messageIndex);
      msgs.push({ role: "user", content: newContent });
      setLocalMessages(msgs);

      const messagesToSend = msgs.filter((m) => m.role === "user" || m.content);
      chat.sendMessage({
        messages: messagesToSend,
        modelAlias: selectedModel,
        conversationId: activeId,
        corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
        reasoningMode,
        onDone: (fullContent) => {
          const updated = [...messagesToSend, { role: "assistant", content: fullContent }];
          setLocalMessages(updated);
          if (activeId === null) {
            conversations.refetch();
          } else {
            conversation.refetch();
          }
        },
      });
    },
    [localMessages, selectedModel, activeId, selectedCorpora, reasoningMode, chat, conversations, conversation],
  );

  return (
    <div className="flex h-full">
      {/* Conversations panel (within content area, not AppLayout sidebar) */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-background">
        <div className="space-y-2 border-b border-border p-3">
          <button
            onClick={handleNew}
            className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Новый диалог
          </button>
          {hasAgentModels && (
            <button
              onClick={handleNewAgent}
              className="flex w-full items-center justify-center gap-1.5 rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground hover:bg-accent"
              data-testid="new-agent-chat"
              title="Диалог, в котором модель сама вызывает инструменты (поиск по корпусу)"
            >
              <Bot className="h-4 w-4" />
              Агентный диалог
            </button>
          )}
        </div>
        <ConversationList
          conversations={conversations.data?.conversations ?? []}
          activeId={activeId}
          onSelect={handleSelect}
          onDelete={handleDeleteConversation}
        />
      </aside>

      {/* Main chat area */}
      <main className="flex flex-1 flex-col">
        {/* Header with model selector */}
        <div className="flex h-12 items-center gap-3 border-b border-border px-4">
          {activeId && conversation.data && (
            <input
              type="text"
              value={conversation.data.title}
              onChange={(e) => {
                updateConv.mutate({ id: activeId, title: e.target.value });
              }}
              className="flex-1 bg-transparent text-sm font-medium text-foreground outline-none"
              placeholder="Заголовок диалога"
            />
          )}
          {!activeId && (
            <span className="flex-1 text-sm text-muted-foreground">
              {agentMode ? "Новый агентный диалог" : "Новый диалог"}
            </span>
          )}
          {/* Т-509 (решение 2): профиль фиксирует модель и скилл диалога,
              поэтому для профильного диалога селекторы не показываются —
              вместо них подпись профиля. Переопределение конфигурации
              сервер отклоняет явно (400 agent_profile_conflict), так что
              скрыть выбор — не косметика, а отражение фактического
              поведения. */}
          {agentMode && agentProfileId ? (
            <span
              className="flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2 py-1.5 text-sm"
              data-testid="agent-profile-badge"
              title="Модель и скилл закреплены профилем агента"
            >
              <Bot className="h-4 w-4 text-muted-foreground" />
              Профиль: {activeProfile?.name ?? "недоступен"}
            </span>
          ) : (
            <>
              <ModelSelector
                models={selectorModels}
                value={selectedModel}
                onChange={setSelectedModel}
                disabled={isBusy}
              />
              {/* Т-508: выбор скилла виден только в агентном режиме и только
                  когда есть из чего выбирать. Выбор приходит на каждый запрос,
                  а не хранится на диалоге (решение 5). Пока ждёт решения по
                  подтверждению деструктивного действия селектор заблокирован:
                  смена скилла могла бы убрать из прогона одобряемый инструмент. */}
              {agentMode && availableSkills.data && availableSkills.data.skills.length > 0 && (
                <select
                  value={selectedSkillId ?? ""}
                  onChange={(e) => setSelectedSkillId(e.target.value || null)}
                  disabled={isBusy || !!agent.pendingConfirmation}
                  data-testid="agent-skill-select"
                  title="Скилл: дополнительные инструкции агенту и разрешённый набор инструментов"
                  className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                >
                  <option value="">Без скилла</option>
                  {selectedSkillId &&
                    !availableSkills.data.skills.some((s) => s.id === selectedSkillId) && (
                      <option value={selectedSkillId}>Скилл недоступен</option>
                    )}
                  {availableSkills.data.skills.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              )}
            </>
          )}
          {/* Т-509 (решение 7): остановка прогона. Запрос ставит флаг на
              сервере, а цикл проверяет его МЕЖДУ шагами — текущий вызов
              модели или инструмента дорабатывает. Отличается от обрыва
              запроса в браузере: прогон завершается штатно, расход
              выполненных шагов сохраняется, лента показывает шаг
              «Прогон остановлен». Кнопка появляется, только когда
              идентификатор диалога уже известен: для самого первого
              сообщения сервер создаёт диалог внутри запроса, и ставить
              флаг ещё не на что. */}
          {agentMode && agent.isRunning && agentConvId && (
            <button
              onClick={() => stopConversation.mutate(agentConvId)}
              disabled={stopConversation.isPending}
              data-testid="agent-stop"
              className="flex shrink-0 items-center gap-1 rounded-md border border-destructive/40 px-2 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-50"
              title="Остановить прогон: текущий шаг доработает, следующий не начнётся"
            >
              <Square className="h-3.5 w-3.5" />
              Остановить
            </button>
          )}
          {/* Т-445 (Г1): переключатель рассуждения виден только при политике
              "optional"; при off/on режим фиксирован политикой. В агентном
              режиме (Т-502) рассуждение не управляется — скрыт. */}
          {reasoningOptional && !agentMode && (
            <label
              className="flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              title="Режим рассуждения модели на это сообщение (политика роли: по выбору)"
            >
              <input
                type="checkbox"
                checked={reasoningOn}
                onChange={(e) => setReasoningOn(e.target.checked)}
                disabled={isBusy}
                className="h-3.5 w-3.5"
                data-testid="reasoning-toggle"
              />
              Рассуждение
            </label>
          )}
          {activeId && conversation.data && (
            <button
              onClick={handleResetContext}
              disabled={isBusy || resetContext.isPending || displayedMessages.length === 0}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
              title="Сбросить контекст: история останется видимой, но в модель уйдут только сообщения после маркера"
            >
              <Eraser className="h-4 w-4" />
            </button>
          )}
          {activeId && conversation.data && (
            <button
              onClick={handleExport}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Экспорт в Markdown"
            >
              <Download className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* T-439: мультивыбор корпусов для RAG */}
        {(availableCorpora.data?.corpora.length ?? 0) > 0 && (
          <div className="border-b border-border px-4 py-2">
            <CorpusSelector
              corpora={availableCorpora.data?.corpora ?? []}
              selected={selectedCorpora}
              onToggle={handleToggleCorpus}
              disabled={isBusy}
            />
          </div>
        )}

        {/* Т-502: сводка шагов последнего агентного прогона */}
        {agentMode && agent.steps.length > 0 && <AgentRunSummary steps={agent.steps} />}
        {/* Т-508 (решение 6): честное сообщение о том, какие инструменты
            выбранного скилла в этом прогоне недоступны — «обещано N,
            доступно M» по образцу усечения Т-504. Причина (сервер
            недоступен, класс данных, инструмент не зарегистрирован) в
            трассировке. */}
        {agentMode && agent.skillToolsUnavailable.length > 0 && (
          <div
            className="mx-4 mb-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400"
            data-testid="agent-skill-tools-unavailable"
          >
            Недоступные инструменты скилла: {agent.skillToolsUnavailable.join(", ")}
          </div>
        )}

        {/* Messages */}
        <ChatMessages
          messages={displayedMessages}
          streamingContent={localMessages.length > 0 && localMessages[localMessages.length - 1].role === "assistant"
            ? localMessages[localMessages.length - 1].content
            : ""}
          streamingReasoning={agentMode ? "" : chat.streamingReasoning}
          isStreaming={isBusy}
          error={agentMode ? agent.error : chat.error}
          sources={agentMode ? agent.sources : chat.sources}
          ragDegraded={agentMode ? false : chat.ragDegraded}
          onRegenerate={localMessages.length > 0 ? handleRegenerate : undefined}
          onEdit={handleEdit}
          contextResetAt={conversation.data?.context_reset_at ?? null}
        />

        {/* Пункт 9: деструктивный инструмент просит подтверждения — прогон
            остановлен до выполнения, действие не выполняется без решения. */}
        {agentMode && agent.pendingConfirmation && (
          <div
            className="mx-4 my-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3"
            data-testid="agent-confirmation-card"
          >
            <p className="text-sm font-medium">
              Инструмент «{agent.pendingConfirmation.tool}» выполняет действие и просит
              подтверждения
            </p>
            <p className="mt-1 break-all text-xs text-muted-foreground">
              Параметры: {JSON.stringify(agent.pendingConfirmation.args)}
            </p>
            <div className="mt-2 flex items-center gap-2">
              <button
                onClick={() => handleAgentConfirmation("approve")}
                disabled={isBusy}
                data-testid="confirmation-approve"
                className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                Выполнить
              </button>
              <button
                onClick={() => handleAgentConfirmation("reject")}
                disabled={isBusy}
                data-testid="confirmation-reject"
                className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent disabled:opacity-50"
              >
                Отменить
              </button>
            </div>
          </div>
        )}

        {/* Т-502: дополнение не установлено — честная причина вместо ввода */}
        {agentMode && agent.unavailableReason ? (
          <div className="border-t border-border px-4 py-3 text-sm text-muted-foreground">
            {agent.unavailableReason}
          </div>
        ) : (
          <ChatInput
            onSend={handleSend}
            onAbort={handleAbort}
            isStreaming={isBusy}
            disabled={!modelReady || (agentMode && !!agent.pendingConfirmation)}
            contextUsage={contextUsage}
            templates={canPrompts ? (promptTemplates.data?.templates ?? []) : []}
          />
        )}
      </main>

      <NewChatModal
        open={newChatOpen}
        models={models.data ?? []}
        onCancel={() => setNewChatOpen(false)}
        onCreate={handleCreateChat}
      />
      {/* Т-502 (решение 10): отдельная точка создания агентного диалога —
          только модели с флагом пригодности к инструментам. */}
      <NewChatModal
        open={newAgentChatOpen}
        models={agentModels}
        title="Агентный диалог"
        description="Модель будет сама вызывать инструменты (поиск по корпусу). Выберите модель с поддержкой инструментов."
        onCancel={() => setNewAgentChatOpen(false)}
        onCreate={handleCreateAgent}
      />
    </div>
  );
}
