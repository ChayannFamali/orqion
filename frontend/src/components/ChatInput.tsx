import { useState, type KeyboardEvent } from "react";
import { Textarea } from "./ui/textarea";
import { Button } from "./ui/button";

interface ChatInputProps {
  onSend: (text: string) => void;
  onAbort: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  /** T-442: занятость окна контекста текущего диалога (оценка токенов). */
  contextUsage?: { used: number; max: number | null } | null;
}

export function ChatInput({ onSend, onAbort, isStreaming, disabled, contextUsage }: ChatInputProps) {
  const [text, setText] = useState("");

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || isStreaming) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-border p-4">
      <div className="mx-auto max-w-3xl">
        {contextUsage && (
          <div
            className="mb-1 text-right text-xs text-muted-foreground"
            title="Оценка занятости окна контекста (символы ÷ 3); обнуляется после сброса контекста"
          >
            ≈ {contextUsage.used.toLocaleString("ru-RU")}
            {" / "}
            {contextUsage.max !== null ? contextUsage.max.toLocaleString("ru-RU") : "∞"} токенов
          </div>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Введите сообщение… (Enter — отправить, Shift+Enter — новая строка)"
            rows={2}
            disabled={disabled}
            className="min-h-[44px] flex-1"
          />
          {isStreaming ? (
            <Button variant="destructive" onClick={onAbort} className="shrink-0">
              Стоп
            </Button>
          ) : (
            <Button
              onClick={handleSend}
              disabled={disabled || !text.trim()}
              className="shrink-0"
            >
              Отправить
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
