import { useState, type KeyboardEvent } from "react";
import { Textarea } from "./ui/textarea";
import { Button } from "./ui/button";

interface ChatInputProps {
  onSend: (text: string) => void;
  onAbort: () => void;
  isStreaming: boolean;
  disabled?: boolean;
}

export function ChatInput({ onSend, onAbort, isStreaming, disabled }: ChatInputProps) {
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
      <div className="mx-auto flex max-w-3xl items-end gap-2">
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
  );
}
