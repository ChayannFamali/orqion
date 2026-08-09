import { useEffect, useRef } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import type { MessageResponse } from "../api/types";
import { cn } from "../lib/utils";

interface ChatMessagesProps {
  messages: MessageResponse[];
  /** Контент активного стрима (ещё не сохранён в БД) */
  streamingContent: string;
  isStreaming: boolean;
  error: { code: string; message: string } | null;
}

export function ChatMessages({
  messages,
  streamingContent,
  isStreaming,
  error,
}: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages.length, streamingContent, isStreaming]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="mx-auto max-w-3xl space-y-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn(
              "rounded-lg px-4 py-3",
              msg.role === "user"
                ? "bg-secondary text-secondary-foreground"
                : "bg-background border border-border",
            )}
          >
            <div className="mb-1 text-xs font-medium text-muted-foreground">
              {msg.role === "user" ? "Вы" : "Ассистент"}
            </div>
            {msg.role === "assistant" ? (
              <MarkdownRenderer content={msg.content} />
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
