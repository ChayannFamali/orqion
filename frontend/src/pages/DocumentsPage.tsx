import { useRef, useState } from "react";
import { ArrowLeft, ClipboardCheck, FileText, GitBranch, Loader2, Trash2, Upload, X } from "lucide-react";
import { useDeleteDocument } from "../hooks/useDocuments";
import { useDocuments } from "../hooks/useDocuments";
import { useUploadDocument } from "../hooks/useDocuments";
import { EvalSetsPage } from "./EvalSetsPage";
import { IndexVersionsPage } from "./IndexVersionsPage";
import type { CorpusResponse } from "../api/types";
import type { UploadProgress } from "../api/documents";

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
  const uploadMutation = useUploadDocument(corpus.id);
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
                      {formatBytes(doc.sha256.length > 0 ? 0 : 0)}
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
          onClose={() => setShowUpload(false)}
          uploadMutation={uploadMutation}
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

function UploadModal({
  corpusId: _corpusId,
  onClose,
  uploadMutation,
}: {
  corpusId: string;
  onClose: () => void;
  uploadMutation: ReturnType<typeof useUploadDocument>;
}) {
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    try {
      await uploadMutation.mutateAsync({
        file,
        onProgress: setProgress,
      });
      onClose();
    } catch {
      // toast via global onError
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Загрузка документа</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        {progress ? (
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>Загрузка…</span>
              <span>{progress.percent}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress.percent}%` }}
              />
            </div>
          </div>
        ) : (
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer.files[0];
              if (file) handleFile(file);
            }}
            className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border p-8"
          >
            <Upload className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Перетащите файл или нажмите для выбора
            </p>
            <button
              onClick={() => inputRef.current?.click()}
              className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Выбрать файл
            </button>
            <input
              ref={inputRef}
              type="file"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
            />
          </div>
        )}

        {uploadMutation.isError && (
          <p className="mt-3 text-sm text-destructive">Ошибка загрузки</p>
        )}
      </div>
    </div>
  );
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
