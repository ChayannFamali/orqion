import { useCallback, useEffect, useState } from "react";
import { Download } from "lucide-react";
import { useConversations, useConversation, useUpdateConversation } from "../hooks/useConversations";
import { useEnabledModels } from "../hooks/useModels";
import { useChat } from "../hooks/useChat";
import { ConversationList } from "../components/ConversationList";
import { ChatMessages } from "../components/ChatMessages";
import { ChatInput } from "../components/ChatInput";
import { ModelSelector } from "../components/ModelSelector";
import { NewChatModal } from "../components/NewChatModal";
import type { ChatMessage, MessageResponse } from "../api/types";
import { conversationToMarkdown, downloadMarkdown, sanitizeFilename } from "../utils/exportConversation";

export function ChatPage() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [newChatOpen, setNewChatOpen] = useState(false);

  const conversations = useConversations();
  const conversation = useConversation(activeId);
  const models = useEnabledModels();
  const updateConv = useUpdateConversation();
  const chat = useChat();

  // Явный дефолт вместо null: селектор всегда показывает модель, которая
  // ответит; запрос уходит с model_alias, а не с неявным candidates[0]
  useEffect(() => {
    if (selectedModel === null && models.data && models.data.length > 0) {
      setSelectedModel(models.data[0].alias);
    }
  }, [models.data, selectedModel]);

  const displayedMessages: MessageResponse[] = conversation.data?.messages ?? [];

  const handleSelect = useCallback((id: string) => {
    setActiveId(id);
    setLocalMessages([]);
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
    [localMessages, selectedModel, activeId, chat, conversations, conversation],
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
  }, [localMessages, selectedModel, activeId, chat, conversations, conversation]);

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
    [localMessages, selectedModel, activeId, chat, conversations, conversation],
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
              onClick={handleExport}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Экспорт в Markdown"
            >
              <Download className="h-4 w-4" />
            </button>
          )}
        </div>

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
        />

        {/* Input */}
        <ChatInput
          onSend={handleSend}
          onAbort={handleAbort}
          isStreaming={chat.isStreaming}
          disabled={models.data?.length === 0}
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
