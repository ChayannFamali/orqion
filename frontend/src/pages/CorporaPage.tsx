import { useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { useCorpora } from "../hooks/useCorpora";
import { useCreateCorpus } from "../hooks/useCorpora";
import type { CorpusResponse } from "../api/types";

const DATA_CLASSES = [
  {
    value: "К0" as const,
    label: "К0 — публичные материалы",
    description: "Внешние и локальные модели разрешены",
  },
  {
    value: "К1" as const,
    label: "К1 — внутренние рабочие материалы",
    description: "Внешние и локальные модели разрешены",
  },
  {
    value: "К2" as const,
    label: "К2 — персональные данные",
    description: "Только локальные модели. Модель фиксируется pinned_model_id",
  },
  {
    value: "К3" as const,
    label: "К3 — коммерческая тайна",
    description: "Только локальные модели. Модель фиксируется pinned_model_id",
  },
];

function dataClassBadge(dataClass: string | null): string {
  if (!dataClass) return "bg-muted text-muted-foreground";
  if (dataClass === "К0") return "bg-primary/10 text-primary";
  if (dataClass === "К1") return "bg-blue-500/10 text-blue-600";
  if (dataClass === "К2") return "bg-orange-500/10 text-orange-600";
  if (dataClass === "К3") return "bg-red-500/10 text-red-600";
  return "bg-muted text-muted-foreground";
}

export function CorporaPage() {
  const { data, isLoading, error } = useCorpora();
  const [showCreateForm, setShowCreateForm] = useState(false);

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
        Ошибка загрузки корпусов
      </div>
    );
  }

  const corpora = data?.corpora ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Корпуса</h2>
        <button
          onClick={() => setShowCreateForm(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Добавить
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {corpora.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет корпусов
          </div>
        ) : (
          <div className="space-y-3">
            {corpora.map((corpus) => (
              <CorpusCard key={corpus.id} corpus={corpus} />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && <CreateCorpusModal onClose={() => setShowCreateForm(false)} />}
    </div>
  );
}

function CorpusCard({ corpus }: { corpus: CorpusResponse }) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{corpus.name}</span>
            <span
              className={
                "rounded px-1.5 py-0.5 text-xs " + dataClassBadge(corpus.data_class)
              }
            >
              {corpus.data_class ?? "без класса"}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">
            {corpus.pinned_model_id && `Модель: ${corpus.pinned_model_id}`}
            {corpus.active_index_version_id
              ? " · Индекс активен"
              : " · Индекс не построен"}
          </div>
        </div>
      </div>
    </div>
  );
}

function CreateCorpusModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateCorpus();
  const [name, setName] = useState("");
  const [dataClass, setDataClass] = useState<"К0" | "К1" | "К2" | "К3" | "">("");
  const [pinnedModelId, setPinnedModelId] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    try {
      await createMutation.mutateAsync({
        name,
        data_class: dataClass || null,
        pinned_model_id: pinnedModelId || null,
      });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  const selectedClass = DATA_CLASSES.find((c) => c.value === dataClass);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Новый корпус</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Имя корпуса</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="public"
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Класс данных</label>
            <select
              value={dataClass}
              onChange={(e) => setDataClass(e.target.value as "К0" | "К1" | "К2" | "К3" | "")}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
            >
              <option value="">Без класса</option>
              {DATA_CLASSES.map((cls) => (
                <option key={cls.value} value={cls.value}>
                  {cls.label}
                </option>
              ))}
            </select>
            {selectedClass && (
              <p className="mt-1 text-xs text-muted-foreground">
                {selectedClass.description}
              </p>
            )}
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">
              Pinned model ID (опционально, для К2/К3)
            </label>
            <input
              type="text"
              value={pinnedModelId}
              onChange={(e) => setPinnedModelId(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="model UUID"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Для К2/К3 фиксирует модель и не даёт пользователю выбирать
            </p>
          </div>

          <button
            type="submit"
            disabled={createMutation.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {createMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Создать
          </button>
        </form>
      </div>
    </div>
  );
}
