import { cn } from "../lib/utils";
import type { ModelInfo } from "../api/types";

interface ModelSelectorProps {
  models: ModelInfo[];
  value: string | null;
  onChange: (alias: string) => void;
  disabled?: boolean;
}

export function ModelSelector({ models, value, onChange, disabled }: ModelSelectorProps) {
  if (models.length === 0) {
    return (
      <span className="text-sm text-muted-foreground">Нет доступных моделей</span>
    );
  }

  return (
    <select
      className={cn(
        "h-9 rounded-md border border-input bg-background px-2 text-sm",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
      )}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
    >
      {models.map((m) => (
        <option key={m.id} value={m.alias}>
          {m.alias}
          {m.locality === "local" ? " (local)" : " (external)"}
        </option>
      ))}
    </select>
  );
}
