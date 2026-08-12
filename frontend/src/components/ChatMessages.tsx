import { useEffect, useRef, useState } from "react";
import { RotateCcw, Pencil, Check, X } from "lucide-react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { Button } from "./ui/button";
import type { MessageResponse } from "../api/types";
import { cn } from "../lib/utils";

interface ChatMessagesProps {
  messages: MessageResponse[];
  /** Контент активного стрима (ещё не сохранён в БД) */
  streamingContent: string;
  isStreaming: boolean;
  error: { code: string; message: string } | null;
  /** Повторить последний запрос (без последнего ответа ассистента) */
  onRegenerate?: () => void;
  /** Редактировать сообщение пользователя и переотправить */
  onEdit?: (messageIndex: number, newContent: string) => void;
}

export function ChatMessages({
  messages,
  streamingContent,
  isStreaming,
  error,
  onRegenerate,
  onEdit,
}: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editText, setEditText] = useState("");

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages.length, streamingContent, isStreaming]);

  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  const startEdit = (idx: number, content: string) => {
    setEditingIdx(idx);
    setEditText(content);
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditText("");
  };

  const saveEdit = () => {
    if (editingIdx !== null && editText.trim()) {
      onEdit?.(editingIdx, editText.trim());
    }
    setEditingIdx(null);
    setEditText("");
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.map((msg, idx) => (
          <div
            key={msg.id}
            className={cn(
              "rounded-lg px-4 py-3",
              msg.role === "user"
                ? "bg-secondary text-secondary-foreground"
                : "bg-background border border-border",
            )}
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">
                {msg.role === "user" ? "Вы" : "Ассистент"}
              </span>
              {msg.role === "user" && !isStreaming && onEdit && editingIdx !== idx && (
                <button
                  onClick={() => startEdit(idx, msg.content)}
                  className="text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                  title="Редактировать"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {editingIdx === idx ? (
              <div className="space-y-2">
                <textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  className="w-full resize-none rounded-md border border-input bg-background p-2 text-sm"
                  rows={3}
                  autoFocus
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={saveEdit} disabled={!editText.trim()}>
                    <Check className="mr-1 h-3.5 w-3.5" />
                    Отправить
                  </Button>
                  <Button size="sm" variant="ghost" onClick={cancelEdit}>
                    <X className="mr-1 h-3.5 w-3.5" />
                    Отмена
                  </Button>
                </div>
              </div>
            ) : msg.role === "assistant" ? (
              <>
                <MarkdownRenderer content={msg.content} />
                {idx === lastAssistantIdx && !isStreaming && onRegenerate && (
                  <button
                    onClick={onRegenerate}
                    className="mt-2 flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
                    title="Повторить"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Повторить
                  </button>
                )}
              </>
            ) : (
              <p className="whitespace-pre-wrap break-words text-sm">{msg.content}</p>
            )}
          </div>
        ))}

        {streamingContent && (
          <div className="rounded-lg border border-border bg-background px-4 py-3">
            <div className="mb-1 text-xs font-medium text-muted-foreground">Ассистент</div>
            <MarkdownRenderer content={streamingContent} />
            {isStreaming && (
              <span className="mt-1 inline-block h-4 w-2 animate-pulse bg-foreground/50" />
            )}
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3">
            <p className="text-sm text-destructive">
              <strong>Ошибка:</strong> {error.message}
              <span className="ml-2 text-xs opacity-70">({error.code})</span>
            </p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
