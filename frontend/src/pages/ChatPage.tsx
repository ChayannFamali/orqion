import { useCallback, useEffect, useState } from "react";
import { Download, Eraser } from "lucide-react";
import {
  useConversations,
  useConversation,
  useUpdateConversation,
  useResetConversationContext,
  useDeleteConversation,
} from "../hooks/useConversations";
import { useEnabledModels } from "../hooks/useModels";
import { useAvailableCorpora } from "../hooks/useCorpora";
import { useChat } from "../hooks/useChat";
import { ConversationList } from "../components/ConversationList";
import { ChatMessages } from "../components/ChatMessages";
import { ChatInput } from "../components/ChatInput";
import { ModelSelector } from "../components/ModelSelector";
import { CorpusSelector } from "../components/CorpusSelector";
import { NewChatModal } from "../components/NewChatModal";
import type { ChatMessage, MessageResponse } from "../api/types";
import { conversationToMarkdown, downloadMarkdown, sanitizeFilename } from "../utils/exportConversation";
import { estimateTokens } from "../utils/estimateTokens";

export function ChatPage() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [newChatOpen, setNewChatOpen] = useState(false);
  // T-439: имена выбранных корпусов для RAG-запроса (мульти-режим)
  const [selectedCorpora, setSelectedCorpora] = useState<string[]>([]);

  const conversations = useConversations();
  const conversation = useConversation(activeId);
  const models = useEnabledModels();
  const availableCorpora = useAvailableCorpora();
  const updateConv = useUpdateConversation();
  const resetContext = useResetConversationContext();
  const deleteConv = useDeleteConversation();
  const chat = useChat();

  // Явный дефолт вместо null: селектор всегда показывает модель, которая
  // ответит; запрос уходит с model_alias, а не с неявным candidates[0]
  useEffect(() => {
    if (selectedModel === null && models.data && models.data.length > 0) {
      setSelectedModel(models.data[0].alias);
    }
  }, [models.data, selectedModel]);

  const displayedMessages: MessageResponse[] = conversation.data?.messages ?? [];

  // T-442: занятость окна контекста = оценка токенов буфера отправки
  // (именно он уходит в модель). После сброса буфер обнуляется → 0.
  const selectedModelMeta = models.data?.find((m) => m.alias === selectedModel);
  const contextUsage = {
    used: localMessages.reduce((acc, m) => acc + estimateTokens(m.content), 0),
    max: selectedModelMeta?.max_input_tokens ?? null,
  };

  const handleSelect = useCallback((id: string) => {
    setActiveId(id);
    setLocalMessages([]);
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
    setNewChatOpen(false);
  }, []);

  const handleSend = useCallback(
    (text: string) => {
      const userMsg: ChatMessage = { role: "user", content: text };
      const assistantMsg: ChatMessage = { role: "assistant", content: "" };

      const messagesToSend = [...localMessages, userMsg];
      setLocalMessages([...messagesToSend, assistantMsg]);

      chat.sendMessage({
        messages: messagesToSend,
        modelAlias: selectedModel,
        conversationId: activeId,
        corpusNames: selectedCorpora.length > 0 ? selectedCorpora : null,
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
    [localMessages, selectedModel, activeId, selectedCorpora, chat, conversations, conversation],
  );

  const handleAbort = useCallback(() => {
    chat.abort();
  }, [chat]);

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
  }, [localMessages, selectedModel, activeId, selectedCorpora, chat, conversations, conversation]);

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
    [localMessages, selectedModel, activeId, selectedCorpora, chat, conversations, conversation],
  );

  return (
    <div className="flex h-full">
      {/* Conversations panel (within content area, not AppLayout sidebar) */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-background">
        <div className="border-b border-border p-3">
          <button
            onClick={handleNew}
            className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Новый диалог
          </button>
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
            <span className="flex-1 text-sm text-muted-foreground">Новый диалог</span>
          )}
          <ModelSelector
            models={models.data ?? []}
            value={selectedModel}
            onChange={setSelectedModel}
            disabled={chat.isStreaming}
          />
          {activeId && conversation.data && (
            <button
              onClick={handleResetContext}
              disabled={chat.isStreaming || resetContext.isPending || displayedMessages.length === 0}
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
              disabled={chat.isStreaming}
            />
          </div>
        )}

        {/* Messages */}
        <ChatMessages
          messages={displayedMessages}
          streamingContent={localMessages.length > 0 && localMessages[localMessages.length - 1].role === "assistant"
            ? localMessages[localMessages.length - 1].content
            : ""}
          isStreaming={chat.isStreaming}
          error={chat.error}
          sources={chat.sources}
          ragDegraded={chat.ragDegraded}
          onRegenerate={localMessages.length > 0 ? handleRegenerate : undefined}
          onEdit={handleEdit}
          contextResetAt={conversation.data?.context_reset_at ?? null}
        />

        {/* Input */}
        <ChatInput
          onSend={handleSend}
          onAbort={handleAbort}
          isStreaming={chat.isStreaming}
          disabled={models.data?.length === 0}
          contextUsage={contextUsage}
        />
      </main>

      <NewChatModal
        open={newChatOpen}
        models={models.data ?? []}
        onCancel={() => setNewChatOpen(false)}
        onCreate={handleCreateChat}
      />
    </div>
  );
}
