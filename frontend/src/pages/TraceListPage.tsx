import { useState } from "react";
import { Loader2, Clock, CheckCircle, XCircle, ChevronRight } from "lucide-react";
import { useTraces } from "../hooks/useTraces";

interface TraceListPageProps {
  onTraceSelect: (traceId: string) => void;
}

export function TraceListPage({ onTraceSelect }: TraceListPageProps) {
  const { data, isLoading, error } = useTraces();
  const [selectedConv, setSelectedConv] = useState<string | undefined>(undefined);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-destructive">
        Ошибка загрузки трассировок
      </div>
    );
  }

  const traces = data?.traces ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Трассировки</h2>
        <p className="text-sm text-muted-foreground">
          Всего: {data?.total ?? 0}
          {selectedConv && (
            <button
              onClick={() => setSelectedConv(undefined)}
              className="ml-2 text-primary hover:underline"
            >
              Сбросить фильтр
            </button>
          )}
        </p>
      </div>
      <div className="flex-1 overflow-y-auto">
        {traces.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет трассировок
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {traces.map((trace) => (
              <li key={trace.id}>
                <button
                  onClick={() => onTraceSelect(trace.id)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent"
                >
                  <div className="shrink-0">
                    {trace.status === "ok" ? (
                      <CheckCircle className="h-4 w-4 text-green-500" />
                    ) : (
                      <XCircle className="h-4 w-4 text-destructive" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium">
                      {new Date(trace.ts).toLocaleString()}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      <span>{trace.total_ms ?? "—"} ms</span>
                      <span>·</span>
                      <span>{trace.span_count} шагов</span>
                    </div>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
