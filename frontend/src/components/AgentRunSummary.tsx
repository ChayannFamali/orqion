import type { AgentStepEntry } from "../api/types";

interface AgentRunSummaryProps {
  /** Шаги последнего агентного прогона */
  steps: AgentStepEntry[];
}

/**
 * Сводка шагов агентного прогона (Т-502). Полная трассировка — в разделе
 * «Трассировки»; здесь компактная лента «что сделал агент» для диалога.
 */
export function AgentRunSummary({ steps }: AgentRunSummaryProps) {
  if (steps.length === 0) return null;
  return (
    <div
      className="mx-4 mb-2 rounded-md border border-border bg-muted/40 px-3 py-2"
      data-testid="agent-run-summary"
    >
      <div className="mb-1 text-xs font-medium text-muted-foreground">Шаги агента</div>
      <ol className="space-y-0.5">
        {steps.map((step) => (
          <li key={step.index} className="flex items-baseline gap-2 text-xs">
            <span className="shrink-0 tabular-nums text-muted-foreground">{step.index}.</span>
            <span className="shrink-0 font-medium">
              {step.kind === "tool" ? `Инструмент${step.name ? ` ${step.name}` : ""}` : "Модель"}
            </span>
            <span className="truncate text-foreground/80">
              {step.summary}
              {step.decision === "deny" ? " (отказ политики)" : ""}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
