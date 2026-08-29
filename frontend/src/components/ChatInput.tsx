import { useState, type KeyboardEvent } from "react";
import { FileText } from "lucide-react";
import { Textarea } from "./ui/textarea";
import { Button } from "./ui/button";

/** Шаблон промпта для быстрого выбора (Т-507). */
export interface PromptTemplateOption {
  id: string;
  title: string;
  body: string;
}

interface ChatInputProps {
  onSend: (text: string) => void;
  onAbort: () => void;
  isStreaming: boolean;
  disabled?: boolean;
  /** T-442: занятость окна контекста текущего диалога (оценка токенов). */
  contextUsage?: { used: number; max: number | null } | null;
  /** Т-507: личные сохранённые промпты пользователя. */
  templates?: PromptTemplateOption[];
}

export function ChatInput({
  onSend,
  onAbort,
  isStreaming,
  disabled,
  contextUsage,
  templates,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);

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

  // Клик по шаблону вставляет его в поле ввода: текст правится и
  // отправляется пользователем (решение дизайн-ревью Т-507). Пустое поле
  // — текст заменяется; непустое — дописывается с новой строки.
  const handlePickTemplate = (body: string) => {
    setText((prev) => (prev.trim() ? `${prev}\n${body}` : body));
    setPickerOpen(false);
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
        {(templates?.length ?? 0) > 0 && (
          <div className="relative mb-1">
            <button
              type="button"
              onClick={() => setPickerOpen((o) => !o)}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
              data-testid="prompt-template-picker"
            >
              <FileText className="h-3.5 w-3.5" />
              Шаблоны
            </button>
            {pickerOpen && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setPickerOpen(false)}
                  aria-hidden="true"
                />
                <ul
                  className="absolute bottom-full left-0 z-20 mb-1 max-h-56 w-full overflow-y-auto rounded-md border border-border bg-card py-1 shadow-lg"
                  data-testid="prompt-template-menu"
                >
                  {(templates ?? []).map((t) => (
                    <li key={t.id}>
                      <button
                        type="button"
                        onClick={() => handlePickTemplate(t.body)}
                        className="w-full px-3 py-1.5 text-left text-sm hover:bg-accent"
                        data-testid="prompt-template-option"
                      >
                        {t.title}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
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
            data-testid="chat-input-textarea"
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
