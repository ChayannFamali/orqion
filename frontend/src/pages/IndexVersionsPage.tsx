import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Loader2,
  RotateCcw,
  Trash2,
  Zap,
} from "lucide-react";
import {
  useActivateIndexVersion,
  useBuildIndexVersion,
  useCleanupRetiredVersions,
  useIndexVersions,
  useRollbackIndexVersion,
} from "../hooks/useIndexVersions";
import type { CorpusResponse } from "../api/types";

function statusBadge(status: string): string {
  if (status === "active") return "bg-green-500/10 text-green-600";
  if (status === "completed") return "bg-blue-500/10 text-blue-600";
  if (status === "building") return "bg-yellow-500/10 text-yellow-600";
  if (status === "interrupted") return "bg-red-500/10 text-red-600";
  if (status === "retired") return "bg-muted text-muted-foreground";
  return "bg-muted text-muted-foreground";
}

interface IndexVersionsPageProps {
  corpus: CorpusResponse;
  onBack: () => void;
}

export function IndexVersionsPage({ corpus, onBack }: IndexVersionsPageProps) {
  const { data, isLoading, error } = useIndexVersions(corpus.id);
  const buildMutation = useBuildIndexVersion(corpus.id);
  const activateMutation = useActivateIndexVersion(corpus.id);
  const rollbackMutation = useRollbackIndexVersion(corpus.id);
  const cleanupMutation = useCleanupRetiredVersions(corpus.id);
  const [activateTarget, setActivateTarget] = useState<string | null>(null);
  const [showRollback, setShowRollback] = useState(false);
  const [showCleanup, setShowCleanup] = useState(false);
  const [activateWarning, setActivateWarning] = useState<string | null>(null);

  const versions = data?.versions ?? [];
  const hasRetired = versions.some((v) => v.status === "retired");

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            {corpus.name}
          </button>
          <span className="text-muted-foreground">/</span>
          <h2 className="text-lg font-semibold">Версии индекса</h2>
        </div>
        <div className="flex items-center gap-2">
          {hasRetired && (
            <button
              onClick={() => setShowCleanup(true)}
              className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
            >
              <Trash2 className="h-4 w-4" />
              Очистить retired
            </button>
          )}
          {corpus.active_index_version_id && (
            <button
              onClick={() => setShowRollback(true)}
              className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
            >
              <RotateCcw className="h-4 w-4" />
              Откатить
            </button>
          )}
          <button
            onClick={() => buildMutation.mutate()}
            disabled={buildMutation.isPending}
            className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {buildMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Zap className="h-4 w-4" />
            )}
            Собрать
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center text-destructive">
            Ошибка загрузки версий
          </div>
        ) : versions.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет версий индекса. Нажмите «Собрать» для создания.
          </div>
        ) : (
          <div className="space-y-3">
            {versions.map((v) => (
              <div
                key={v.id}
                className="rounded-lg border border-border p-4"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span
                      className={"rounded px-2 py-0.5 text-xs font-medium " + statusBadge(v.status)}
                    >
                      {v.status}
                    </span>
                    {v.status === "active" && (
                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                    )}
                    {v.status === "building" && (
                      <Clock className="h-4 w-4 text-yellow-600" />
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {v.status === "completed" && (
                      <button
                        onClick={() => {
                          setActivateTarget(v.id);
                          setActivateWarning(null);
                        }}
                        className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground transition-colors hover:bg-primary/90"
                      >
                        Активировать
                      </button>
                    )}
                  </div>
                </div>

                <div className="mt-2 text-xs text-muted-foreground">
                  <span>Модель: {v.embedding_model}</span>
                  <span className="mx-2">·</span>
                  <span>Чанкер: {v.chunker} v{v.chunker_version}</span>
                </div>

                {v.stats && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    {String(v.stats.status) === "building" && !!v.stats.current_document && (
                      <span>Обработка: {String(v.stats.current_document)}</span>
                    )}
                    {String(v.stats.status) === "completed" && (
                      <span>
                        Документов: {Number(v.stats.documents_done)}/{Number(v.stats.documents_total)},
                        чанков: {Number(v.stats.chunks_total)}
                      </span>
                    )}
                    {String(v.stats.status) === "interrupted" && !!v.stats.error && (
                      <span className="text-destructive">
                        Ошибка: {String(v.stats.error)}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {activateTarget && (
        <ActivateConfirmDialog
          onCancel={() => setActivateTarget(null)}
          onConfirm={async () => {
            try {
              const result = await activateMutation.mutateAsync(activateTarget);
              setActivateWarning(result.warning ?? null);
              if (!result.warning) {
                setActivateTarget(null);
              }
            } catch {
              // toast via global onError
            }
          }}
          isPending={activateMutation.isPending}
          warning={activateWarning}
        />
      )}

      {showRollback && (
        <RollbackConfirmDialog
          onCancel={() => setShowRollback(false)}
          onConfirm={async () => {
            try {
              await rollbackMutation.mutateAsync();
              setShowRollback(false);
            } catch {
              // toast via global onError
            }
          }}
          isPending={rollbackMutation.isPending}
        />
      )}

      {showCleanup && (
        <CleanupConfirmDialog
          onCancel={() => setShowCleanup(false)}
          onConfirm={async () => {
            try {
              await cleanupMutation.mutateAsync();
              setShowCleanup(false);
            } catch {
              // toast via global onError
            }
          }}
          isPending={cleanupMutation.isPending}
        />
      )}
    </div>
  );
}

function ActivateConfirmDialog({
  onCancel,
  onConfirm,
  isPending,
  warning,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
  warning: string | null;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-lg font-semibold">Активировать версию?</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Текущая активная версия будет переведена в retired.
        </p>
        {warning && (
          <div className="mb-4 flex items-start gap-2 rounded-md border border-yellow-500/30 bg-yellow-500/5 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-yellow-600" />
            <p className="text-sm text-yellow-700">{warning}</p>
          </div>
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
            disabled={isPending}
            className="flex items-center gap-1 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Активировать
          </button>
        </div>
      </div>
    </div>
  );
}

function RollbackConfirmDialog({
  onCancel,
  onConfirm,
  isPending,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-lg font-semibold">Откатить версию?</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Текущая активная версия будет переведена в retired,
          предыдущая — восстановлена как active.
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-md px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={isPending}
            className="flex items-center gap-1 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Откатить
          </button>
        </div>
      </div>
    </div>
  );
}

function CleanupConfirmDialog({
  onCancel,
  onConfirm,
  isPending,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  isPending: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-lg font-semibold">Удалить retired-версии?</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Retired-версии будут удалены вместе с чанками и векторами.
          Откат к ним станет невозможен.
        </p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-md px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={isPending}
            className="flex items-center gap-1 rounded-md bg-destructive px-4 py-2 text-sm text-destructive-foreground transition-colors hover:bg-destructive/90"
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}
