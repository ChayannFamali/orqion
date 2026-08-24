import { cn } from "../lib/utils";
import type { AvailableCorpusEntry } from "../api/types";

interface CorpusSelectorProps {
  corpora: AvailableCorpusEntry[];
  /** Имена выбранных корпусов. */
  selected: string[];
  onToggle: (name: string) => void;
  disabled?: boolean;
}

/**
 * T-439: мультивыбор корпусов для RAG-запроса.
 *
 * Каждый корпус — переключаемый чип. Пустой выбор = обычный чат без RAG.
 * Неготовые корпуса (нет активной версии индекса) показаны неактивными:
 * запрос с ними упадёт на сервере (fail-closed), поэтому в UI их не дать.
 */
export function CorpusSelector({ corpora, selected, onToggle, disabled }: CorpusSelectorProps) {
  if (corpora.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="corpus-selector">
      <span className="text-xs text-muted-foreground">Корпуса:</span>
      {corpora.map((c) => {
        const active = selected.includes(c.name);
        const blocked = !c.ready;
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => !blocked && onToggle(c.name)}
            disabled={disabled || blocked}
            title={
              blocked
                ? "Корпус не готов: нет активной версии индекса"
                : active
                  ? "Убрать корпус из запроса"
                  : "Добавить корпус в запрос"
            }
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs transition-colors",
              active
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-background text-foreground hover:bg-accent",
              (disabled || blocked) && "cursor-not-allowed opacity-50",
            )}
          >
            {c.name}
            {c.data_class ? ` · ${c.data_class}` : ""}
          </button>
        );
      })}
    </div>
  );
}
