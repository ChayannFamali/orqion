import type { AgentStepEntry } from "../api/types";

interface AgentRunSummaryProps {
  /** Шаги последнего агентного прогона */
  steps: AgentStepEntry[];
}

/**
 * Подписи видов шагов. Явное сопоставление вместо бинарного выбора
 * «инструмент / иначе модель»: при появлении новых видов шагов
 * (``confirmation`` в Т-503, ``skill`` в Т-508) тернарник подписывал их
 * «Моделью» — в ленте читалось «N. Модель Ожидает подтверждения
 * пользователя». Неизвестный вид показывается как есть, чтобы новый шаг
 * не был молча подписан чужим именем.
 */
const STEP_LABELS: Record<string, string> = {
  model: "Модель",
  tool: "Инструмент",
  confirmation: "Подтверждение",
  skill: "Скилл",
};

/**
 * Пояснение к отказу политики. Прочие решения (``pending``/``approve``/
 * ``reject``) здесь не подписываются: их ``summary`` уже говорит о решении
 * («Ожидает подтверждения пользователя», «Отменено пользователем»), и
 * вторая подпись была бы дублем.
 */
const DECISION_LABELS: Record<string, string> = {
  deny: "(отказ политики)",
};

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
        {steps.map((step) => {
          const label = STEP_LABELS[step.kind] ?? step.kind;
          const decisionNote = step.decision ? DECISION_LABELS[step.decision] ?? "" : "";
          return (
            <li key={step.index} className="flex items-baseline gap-2 text-xs">
              <span className="shrink-0 tabular-nums text-muted-foreground">{step.index}.</span>
              <span className="shrink-0 font-medium">
                {step.name ? `${label} ${step.name}` : label}
              </span>
              <span className="truncate text-foreground/80">
                {step.summary}
                {decisionNote ? ` ${decisionNote}` : ""}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
