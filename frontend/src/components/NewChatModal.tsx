import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { cn } from "../lib/utils";
import type { ModelInfo } from "../api/types";

interface NewChatModalProps {
  open: boolean;
  models: ModelInfo[];
  onCancel: () => void;
  onCreate: (alias: string) => void;
  /** Т-502: заголовок точки создания (для агентного диалога — свой). */
  title?: string;
  /** Т-502: подпись-подсказка под заголовком. */
  description?: string;
}

const GROUP_THRESHOLD = 10;

export function NewChatModal({
  open,
  models,
  onCancel,
  onCreate,
  title = "Новый диалог",
  description = "Выберите модель — выбор обязателен",
}: NewChatModalProps) {
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setSelected(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const groups: { kind: string; items: ModelInfo[] }[] = [];
  if (models.length > GROUP_THRESHOLD) {
    for (const m of models) {
      const kind = m.provider_kind ?? "other";
      const g = groups.find((x) => x.kind === kind);
      if (g) {
        g.items.push(m);
      } else {
        groups.push({ kind, items: [m] });
      }
    }
  }

  const row = (m: ModelInfo) => (
    <button
      key={m.id}
      onClick={() => setSelected(m.alias)}
      className={cn(
        "flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm",
        "hover:bg-accent",
        selected === m.alias && "bg-accent text-foreground",
      )}
    >
      <span className="truncate font-medium">{m.alias}</span>
      <span className="shrink-0 text-xs text-muted-foreground">
        {m.provider_kind ? `${m.provider_kind} · ` : ""}
        {m.locality === "local" ? "local" : "external"}
      </span>
    </button>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-md rounded-lg border border-border bg-background p-4 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-1 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button
            onClick={onCancel}
            className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Закрыть"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-sm text-muted-foreground">{description}</p>
        <div className="max-h-80 overflow-y-auto rounded-md border border-border p-1">
          {groups.length > 0
            ? groups.map((g) => (
                <div key={g.kind}>
                  <div className="px-3 pb-1 pt-2 text-xs uppercase tracking-wide text-muted-foreground">
                    {g.kind}
                  </div>
                  {g.items.map(row)}
                </div>
              ))
            : models.map(row)}
        </div>
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-md border border-border px-3 py-2 text-sm hover:bg-accent"
          >
            Отмена
          </button>
          <button
            onClick={() => selected !== null && onCreate(selected)}
            disabled={selected === null}
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Создать диалог
          </button>
        </div>
      </div>
    </div>
  );
}
