import { useState } from "react";
import { ArrowLeft, GitCompare, Loader2, Play, X } from "lucide-react";
import {
  useCompareEvalRuns,
  useCreateEvalRun,
  useEvalRuns,
} from "../hooks/useEval";
import { useIndexVersions } from "../hooks/useIndexVersions";
import type { CorpusResponse } from "../api/types";

interface EvalRunsPageProps {
  corpus: CorpusResponse;
  evalSetId: string;
  onBack: () => void;
}

function formatMetric(value: unknown): string {
  if (typeof value === "number") {
    return value.toFixed(4);
  }
  return String(value);
}

export function EvalRunsPage({ corpus, evalSetId, onBack }: EvalRunsPageProps) {
  const { data, isLoading, error } = useEvalRuns(evalSetId);
  const { data: versionsData } = useIndexVersions(corpus.id);
  const createRunMutation = useCreateEvalRun(evalSetId);
  const compareMutation = useCompareEvalRuns();
  const [showRunDialog, setShowRunDialog] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [selectedRunIds, setSelectedRunIds] = useState<Set<string>>(new Set());
  const [comparison, setComparison] = useState<
    { metric_name: string; earlier_value: number; later_value: number; delta: number; direction: string }[] | null
  >(null);

  const runs = data?.items ?? [];
  const versions = versionsData?.versions ?? [];

  const handleCompare = async () => {
    if (selectedRunIds.size < 2) return;
    try {
      const result = await compareMutation.mutateAsync({
        run_ids: Array.from(selectedRunIds),
      });
      setComparison(result.deltas);
    } catch {
      // toast via global onError
    }
  };

  const toggleRun = (runId: string) => {
    const next = new Set(selectedRunIds);
    if (next.has(runId)) {
      next.delete(runId);
    } else {
      next.add(runId);
    }
    setSelectedRunIds(next);
    setComparison(null);
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Набор
          </button>
          <span className="text-muted-foreground">/</span>
          <h2 className="text-lg font-semibold">Прогоны оценки</h2>
        </div>
        <div className="flex items-center gap-2">
          {selectedRunIds.size >= 2 && (
            <button
              onClick={handleCompare}
              disabled={compareMutation.isPending}
              className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
            >
              {compareMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <GitCompare className="h-4 w-4" />
              )}
              Сравнить ({selectedRunIds.size})
            </button>
          )}
          <button
            onClick={() => setShowRunDialog(true)}
            className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <Play className="h-4 w-4" />
            Запустить
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="text-destructive">Ошибка загрузки прогонов</div>
        ) : runs.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет прогонов. Нажмите «Запустить» для создания.
          </div>
        ) : (
          <div className="space-y-3">
            {runs.map((run) => {
              const isSelected = selectedRunIds.has(run.id);
              const metrics = run.metrics;
              return (
                <div
                  key={run.id}
                  className={
                    "rounded-lg border p-4 transition-colors " +
                    (isSelected
                      ? "border-primary bg-primary/5"
                      : "border-border")
                  }
                >
                  <div className="flex items-start justify-between">
                    <button
                      onClick={() => toggleRun(run.id)}
                      className="flex flex-1 items-center gap-3 text-left"
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleRun(run.id)}
                        className="h-4 w-4"
                      />
                      <div>
                        <div className="text-sm font-medium">
                          {new Date(run.ts).toLocaleString()}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          Версия: {run.index_version_id?.slice(0, 8) ?? "—"}
                        </div>
                      </div>
                    </button>
                  </div>
                  {metrics && (
                    <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                      {Object.entries(metrics)
                        .filter(([, v]) => typeof v === "number")
                        .map(([key, value]) => (
                          <div key={key} className="rounded border border-border px-2 py-1">
                            <span className="text-muted-foreground">{key}: </span>
                            <span className="font-mono">{formatMetric(value)}</span>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {comparison && (
        <div className="border-t border-border p-4">
          <h3 className="mb-2 text-sm font-semibold">Сравнение метрик</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-2 py-1 text-left">Метрика</th>
                  <th className="px-2 py-1 text-right">Было</th>
                  <th className="px-2 py-1 text-right">Стало</th>
                  <th className="px-2 py-1 text-right">Δ</th>
                  <th className="px-2 py-1 text-center">Направление</th>
                </tr>
              </thead>
              <tbody>
                {comparison.map((d) => (
                  <tr key={d.metric_name} className="border-b border-border">
                    <td className="px-2 py-1">{d.metric_name}</td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatMetric(d.earlier_value)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatMetric(d.later_value)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatMetric(d.delta)}
                    </td>
                    <td className="px-2 py-1 text-center">{d.direction}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showRunDialog && (
        <RunDialog
          versions={versions.map((v) => ({ id: v.id, status: v.status }))}
          selectedVersion={selectedVersion}
          onSelect={setSelectedVersion}
          onCancel={() => setShowRunDialog(false)}
          onConfirm={async () => {
            if (!selectedVersion) return;
            try {
              await createRunMutation.mutateAsync({
                index_version_id: selectedVersion,
              });
              setShowRunDialog(false);
              setSelectedVersion(null);
            } catch {
              // toast via global onError
            }
          }}
          isPending={createRunMutation.isPending}
        />
      )}
    </div>
  );
}

function RunDialog({
  versions,
  selectedVersion,
  onSelect,
  onCancel,
  onConfirm,
  isPending,
}: {
  versions: { id: string; status: string }[];
  selectedVersion: string | null;
  onSelect: (id: string | null) => void;
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  const completedVersions = versions.filter((v) => v.status === "completed");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Запуск прогона</h3>
          <button onClick={onCancel}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        {completedVersions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Нет завершённых версий индекса. Постройте и дождитесь завершения
            сборки индекса перед запуском оценки.
          </p>
        ) : (
          <>
            <label className="mb-1 block text-sm font-medium">
              Версия индекса
            </label>
            <select
              value={selectedVersion ?? ""}
              onChange={(e) => onSelect(e.target.value || null)}
              className="mb-4 w-full rounded-md border border-border px-3 py-2 text-sm"
            >
              <option value="">Выберите версию...</option>
              {completedVersions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.id.slice(0, 8)}… ({v.status})
                </option>
              ))}
            </select>
          </>
        )}

        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-md px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={!selectedVersion || isPending}
            className="flex items-center gap-1 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Запустить
          </button>
        </div>
      </div>
    </div>
  );
}
