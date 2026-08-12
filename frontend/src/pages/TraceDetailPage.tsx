import { Loader2, ArrowLeft, Clock, Layers, AlertTriangle } from "lucide-react";
import { useTraceDetail } from "../hooks/useTraces";
import type { SpanResponse } from "../api/types";

interface TraceDetailPageProps {
  traceId: string;
  onBack: () => void;
}

export function TraceDetailPage({ traceId, onBack }: TraceDetailPageProps) {
  const { data, isLoading, error } = useTraceDetail(traceId);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <p className="text-destructive">Трассировка не найдена</p>
        <button onClick={onBack} className="text-primary hover:underline">
          ← Назад к списку
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b border-border px-4 py-3">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Назад
        </button>
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">{data.total_ms ?? "—"} ms</span>
          <span
            className={
              "rounded px-1.5 py-0.5 text-xs " +
              (data.status === "ok"
                ? "bg-green-500/10 text-green-600"
                : "bg-destructive/10 text-destructive")
            }
          >
            {data.status}
          </span>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="space-y-2">
          {data.spans.map((span) => (
            <SpanCard key={span.id} span={span} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SpanCard({ span }: { span: SpanResponse }) {
  const isStep = span.name.startsWith("step_");
  const isRouting = span.name === "routing";
  const isNested = span.name === "rewrite" || span.name === "build_context";

  return (
    <div
      className={
        "rounded-lg border px-4 py-3 " +
        (isStep
          ? "border-primary/30 bg-primary/5"
          : isRouting
            ? "border-amber-500/30 bg-amber-500/5"
            : isNested
              ? "border-border bg-muted/30"
              : "border-border bg-background")
      }
    >
      <div className="flex items-center gap-2">
        <Layers className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="text-sm font-medium">{span.name}</span>
        {span.duration_ms !== null && (
          <span className="text-xs text-muted-foreground">{span.duration_ms} ms</span>
        )}
      </div>
      <SpanPayload span={span} />
    </div>
  );
}

function SpanPayload({ span }: { span: SpanResponse }) {
  const payload = span.payload;

  if (!payload || Object.keys(payload).length === 0) {
    return (
      <p className="mt-2 text-xs italic text-muted-foreground">нет данных (payload пуст)</p>
    );
  }

  // Routing span
  if (span.name === "routing") {
    return (
      <div className="mt-2 space-y-1 text-xs">
        <div>
          <span className="text-muted-foreground">Модель:</span> {payload.model as string}
        </div>
        <div>
          <span className="text-muted-foreground">Правило:</span>{" "}
          {payload.rule_index as number} ({payload.reason as string})
        </div>
        {(payload.fallbacks as string[])?.length > 0 && (
          <div>
            <span className="text-muted-foreground">Fallback:</span>{" "}
            {(payload.fallbacks as string[]).join(", ")}
          </div>
        )}
      </div>
    );
  }

  // Step spans with candidates/reranked
  if (span.name === "step_search" && "candidates" in payload) {
    const candidates = payload.candidates as Record<string, unknown>[];
    return (
      <div className="mt-2 space-y-1 text-xs">
        <div className="text-muted-foreground">
          Кандидатов: {payload.candidates_count as number}
        </div>
        {candidates.length > 0 && (
          <details>
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Показать кандидатов
            </summary>
            <ul className="mt-1 space-y-0.5 pl-4">
              {candidates.slice(0, 10).map((c, i) => (
                <li key={i} className="text-muted-foreground">
                  {i + 1}. score={String(c.score)} dense={c.dense_rank != null ? String(c.dense_rank) : "—"} sparse=
                  {c.sparse_rank != null ? String(c.sparse_rank) : "—"}
                </li>
              ))}
              {candidates.length > 10 && (
                <li className="text-muted-foreground">...и ещё {candidates.length - 10}</li>
              )}
            </ul>
          </details>
        )}
      </div>
    );
  }

  if (span.name === "step_rerank" && "reranked" in payload) {
    const reranked = payload.reranked as Record<string, unknown>[];
    return (
      <div className="mt-2 space-y-1 text-xs">
        <div className="text-muted-foreground">
          Реранжировано: {payload.reranked_count as number}
        </div>
        {reranked.length > 0 && (
          <details>
            <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
              Показать результаты
            </summary>
            <ul className="mt-1 space-y-0.5 pl-4">
              {reranked.map((r, i) => (
                <li key={i} className="text-muted-foreground">
                  {i + 1}. score={String(r.score)} orig_rank={String(r.original_rank)}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }

  // Degraded steps
  if (payload.degraded === true) {
    const errors = payload.errors as string[];
    return (
      <div className="mt-2 flex items-start gap-2 rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-600 dark:text-amber-400">
        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
        <span>
          Деградация
          {errors?.length > 0 ? `: ${errors.join(", ")}` : ""}
        </span>
      </div>
    );
  }

  // Generic payload display
  return (
    <pre className="mt-2 overflow-x-auto text-xs text-muted-foreground">
      {JSON.stringify(payload, null, 2)}
    </pre>
  );
}
