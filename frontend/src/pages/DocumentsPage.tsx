import { useCallback, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  GitBranch,
  Loader2,
  Trash2,
  Upload,
  X,
  XCircle,
} from "lucide-react";
import { useDeleteDocument } from "../hooks/useDocuments";
import { useDocuments } from "../hooks/useDocuments";
import { useQueryClient } from "@tanstack/react-query";
import { apiUploadDocument } from "../api/documents";
import { queryKeys } from "../api/query-keys";
import { EvalSetsPage } from "./EvalSetsPage";
import { IndexVersionsPage } from "./IndexVersionsPage";
import type { CorpusResponse } from "../api/types";

function statusBadge(status: string): string {
  if (status === "ready") return "bg-green-500/10 text-green-600";
  if (status === "indexing") return "bg-blue-500/10 text-blue-600";
  if (status === "pending") return "bg-yellow-500/10 text-yellow-600";
  if (status === "failed") return "bg-red-500/10 text-red-600";
  return "bg-muted text-muted-foreground";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

interface DocumentsPageProps {
  corpus: CorpusResponse;
  capabilities: string[];
  onBack: () => void;
}

export function DocumentsPage({ corpus, capabilities, onBack }: DocumentsPageProps) {
  const { data, isLoading, error } = useDocuments(corpus.id);
  const queryClient = useQueryClient();
  const deleteMutation = useDeleteDocument(corpus.id);
  const [showUpload, setShowUpload] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [showVersions, setShowVersions] = useState(false);
  const [showEval, setShowEval] = useState(false);

  if (showVersions) {
    return <IndexVersionsPage corpus={corpus} onBack={() => setShowVersions(false)} />;
  }

  if (showEval) {
    return <EvalSetsPage corpus={corpus} onBack={() => setShowEval(false)} />;
  }

  const documents = data?.documents ?? [];
  const canManage = capabilities.includes("*") || capabilities.includes("manage_corpora");

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Корпуса
          </button>
          <span className="text-muted-foreground">/</span>
          <h2 className="text-lg font-semibold">{corpus.name}</h2>
        </div>
        <div className="flex items-center gap-2">
          {canManage && (
            <>
              <button
                onClick={() => setShowVersions(true)}
                className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
              >
                <GitBranch className="h-4 w-4" />
                Версии индекса
              </button>
              <button
                onClick={() => setShowEval(true)}
                className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent"
              >
                <ClipboardCheck className="h-4 w-4" />
                Оценка качества
              </button>
            </>
          )}
          <button
            onClick={() => setShowUpload(true)}
            className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <Upload className="h-4 w-4" />
            Загрузить
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
            Ошибка загрузки документов
          </div>
        ) : documents.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет документов. Загрузите первый файл.
          </div>
        ) : (
          <div className="space-y-2">
            {documents.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center justify-between rounded-lg border border-border p-3"
              >
                <div className="flex items-center gap-3">
                  <FileText className="h-5 w-5 text-muted-foreground" />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{doc.filename}</span>
                      <span
                        className={"rounded px-1.5 py-0.5 text-xs " + statusBadge(doc.status)}
                      >
                        {doc.status}
                      </span>
                    </div>
                    {doc.error && (
                      <div className="text-xs text-destructive">{doc.error}</div>
                    )}
                    <div className="text-xs text-muted-foreground">
                      {doc.size_bytes != null ? formatBytes(doc.size_bytes) : "размер неизвестен"}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <a
                    href={`/api/documents/${doc.id}/content`}
                    className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть
                  </a>
                  <button
                    onClick={() => setDeleteTarget(doc.id)}
                    className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showUpload && (
        <UploadModal
          corpusId={corpus.id}
          onClose={() => {
            setShowUpload(false);
            queryClient.invalidateQueries({ queryKey: queryKeys.documents.byCorpus(corpus.id) });
          }}
        />
      )}

      {deleteTarget && (
        <DeleteConfirmDialog
          documentId={deleteTarget}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={async () => {
            try {
              await deleteMutation.mutateAsync(deleteTarget);
              setDeleteTarget(null);
            } catch {
              // toast via global onError
            }
          }}
          isPending={deleteMutation.isPending}
        />
      )}
    </div>
  );
}

type FileStatus = "queued" | "uploading" | "success" | "error" | "cancelled";

interface FileUploadState {
  file: File;
  progress: number;
  status: FileStatus;
  error?: string;
  controller?: AbortController;
}

const MAX_CONCURRENCY = 3;

function UploadModal({
  corpusId,
  onClose,
}: {
  corpusId: string;
  onClose: () => void;
}) {
  const [files, setFiles] = useState<FileUploadState[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    const fileArray = Array.from(newFiles);
    setFiles((prev) => [
      ...prev,
      ...fileArray.map((file) => ({
        file,
        progress: 0,
        status: "queued" as FileStatus,
      })),
    ]);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const dropped = e.dataTransfer;
      if (dropped.items && dropped.items.length > 0) {
        const collected: File[] = [];
        const entries = Array.from(dropped.items)
          .map((item) => item.webkitGetAsEntry?.())
          .filter((entry): entry is FileSystemEntry => entry != null);
        if (entries.length > 0) {
          void collectEntries(entries, collected).then(() => addFiles(collected));
        } else {
          addFiles(dropped.files);
        }
      } else {
        addFiles(dropped.files);
      }
    },
    [addFiles],
  );

  const processQueue = useCallback(async () => {
    setIsProcessing(true);
    const queue = files.filter((f) => f.status === "queued");
    let active = 0;
    let idx = 0;

    await new Promise<void>((resolve) => {
      const next = () => {
        if (idx >= queue.length && active === 0) {
          resolve();
          return;
        }
        while (active < MAX_CONCURRENCY && idx < queue.length) {
          const fileState = queue[idx];
          idx++;
          if (fileState.status !== "queued") continue;
          active++;
          void uploadOne(fileState).finally(() => {
            active--;
            next();
          });
        }
      };
      next();
    });

    setIsProcessing(false);
  }, [files]);

  const uploadOne = useCallback(
    async (state: FileUploadState) => {
      const controller = new AbortController();
      setFiles((prev) =>
        prev.map((f) =>
          f.file === state.file
            ? { ...f, status: "uploading", progress: 0, controller }
            : f,
        ),
      );

      try {
        await apiUploadDocument(
          corpusId,
          state.file,
          (p) => {
            setFiles((prev) =>
              prev.map((f) =>
                f.file === state.file ? { ...f, progress: p.percent } : f,
              ),
            );
          },
          controller.signal,
        );
        setFiles((prev) =>
          prev.map((f) =>
            f.file === state.file ? { ...f, status: "success", progress: 100 } : f,
          ),
        );
      } catch (err) {
        const error = err as { error?: string; reason?: string };
        if (error?.error === "cancelled") {
          setFiles((prev) =>
            prev.map((f) =>
              f.file === state.file ? { ...f, status: "cancelled" } : f,
            ),
          );
        } else {
          setFiles((prev) =>
            prev.map((f) =>
              f.file === state.file
                ? { ...f, status: "error", error: error?.reason ?? "Ошибка" }
                : f,
            ),
          );
        }
      }
    },
    [corpusId],
  );

  const cancelAll = useCallback(() => {
    setFiles((prev) =>
      prev.map((f) => {
        if (f.controller && (f.status === "uploading" || f.status === "queued")) {
          f.controller.abort();
          return { ...f, status: "cancelled" as FileStatus };
        }
        if (f.status === "queued") {
          return { ...f, status: "cancelled" as FileStatus };
        }
        return f;
      }),
    );
  }, []);

  const removeFile = useCallback((file: File) => {
    setFiles((prev) => prev.filter((f) => f.file !== file));
  }, []);

  const successCount = files.filter((f) => f.status === "success").length;
  const errorCount = files.filter((f) => f.status === "error").length;
  const cancelledCount = files.filter((f) => f.status === "cancelled").length;
  const allDone = files.length > 0 && files.every(
    (f) => f.status === "success" || f.status === "error" || f.status === "cancelled",
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={allDone ? onClose : undefined}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">
            {files.length > 0 ? `Загрузка (${files.length})` : "Загрузка документов"}
          </h3>
          <button onClick={allDone ? onClose : cancelAll}>
            {allDone ? (
              <X className="h-4 w-4 text-muted-foreground" />
            ) : (
              <span className="text-sm text-muted-foreground">Отменить все</span>
            )}
          </button>
        </div>

        {files.length === 0 ? (
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
            className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border p-8"
          >
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Перетащите файлы или папку, или нажмите для выбора
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => inputRef.current?.click()}
                className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Выбрать файлы
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="rounded-md border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent"
              >
                Выбрать папку
              </button>
            </div>
            <input
              ref={inputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
              }}
            />
            <input
              ref={fileInputRef}
              type="file"
              multiple
              // @ts-expect-error -- webkitdirectory is non-standard but widely supported
              webkitdirectory=""
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
              }}
            />
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto">
              <div className="space-y-2">
                {files.map((f, i) => (
                  <div
                    key={`${f.file.name}-${i}`}
                    className="flex items-center gap-3 rounded-md border border-border p-2"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium">
                          {f.file.name}
                        </span>
                        <StatusIcon status={f.status} />
                      </div>
                      {(f.status === "uploading" || f.status === "queued") && (
                        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full bg-primary transition-all"
                            style={{ width: `${f.progress}%` }}
                          />
                        </div>
                      )}
                      {f.error && (
                        <p className="mt-0.5 text-xs text-destructive">{f.error}</p>
                      )}
                    </div>
                    {f.status === "queued" && (
                      <button
                        onClick={() => removeFile(f.file)}
                        className="rounded p-1 text-muted-foreground hover:bg-accent"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {allDone ? (
              <div className="mt-4 flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  {successCount} загружено
                  {errorCount > 0 && `, ${errorCount} ошибок`}
                  {cancelledCount > 0 && `, ${cancelledCount} отменено`}
                </span>
                <button
                  onClick={onClose}
                  className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
                >
                  Закрыть
                </button>
              </div>
            ) : (
              <div className="mt-4 flex items-center justify-between">
                <span className="text-sm text-muted-foreground">
                  {successCount} / {files.length} загружено
                </span>
                {!isProcessing && files.some((f) => f.status === "queued") && (
                  <button
                    onClick={processQueue}
                    className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
                  >
                    Начать загрузку
                  </button>
                )}
                {isProcessing && (
                  <button
                    onClick={cancelAll}
                    className="rounded-md border border-border px-4 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent"
                  >
                    Отменить
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: FileStatus }) {
  if (status === "success")
    return <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-green-600" />;
  if (status === "error")
    return <AlertCircle className="h-4 w-4 flex-shrink-0 text-destructive" />;
  if (status === "cancelled")
    return <XCircle className="h-4 w-4 flex-shrink-0 text-muted-foreground" />;
  if (status === "uploading")
    return <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin text-blue-600" />;
  return null;
}

async function collectEntries(
  entries: FileSystemEntry[],
  collected: File[],
): Promise<void> {
  for (const entry of entries) {
    if (entry.isFile) {
      const file = await new Promise<File | null>((resolve) => {
        (entry as FileSystemFileEntry).file(resolve, () => resolve(null));
      });
      if (file) collected.push(file);
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const children = await new Promise<FileSystemEntry[]>((resolve) => {
        const all: FileSystemEntry[] = [];
        const readBatch = () => {
          reader.readEntries(
            (batch) => {
              if (batch.length === 0) {
                resolve(all);
              } else {
                all.push(...batch);
                readBatch();
              }
            },
            () => resolve(all),
          );
        };
        readBatch();
      });
      await collectEntries(children, collected);
    }
  }
}

function DeleteConfirmDialog({
  documentId: _documentId,
  onCancel,
  onConfirm,
  isPending,
}: {
  documentId: string;
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
        <h3 className="mb-2 text-lg font-semibold">Удалить документ?</h3>
        <p className="mb-4 text-sm text-muted-foreground">
          Документ будет удалён. Оригинал файла останется в хранилище.
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
