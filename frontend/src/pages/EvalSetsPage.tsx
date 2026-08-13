import { useState } from "react";
import { ArrowLeft, ClipboardList, Loader2, Plus, Trash2, X } from "lucide-react";
import {
  useCreateEvalItem,
  useCreateEvalSet,
  useDeleteEvalItem,
  useDeleteEvalSet,
  useEvalSet,
  useEvalSets,
} from "../hooks/useEval";
import type { CorpusResponse } from "../api/types";

interface EvalSetsPageProps {
  corpus: CorpusResponse;
  onBack: () => void;
}

export function EvalSetsPage({ corpus, onBack }: EvalSetsPageProps) {
  const { data, isLoading, error } = useEvalSets(corpus.id);
  const [showCreate, setShowCreate] = useState(false);
  const [selectedSetId, setSelectedSetId] = useState<string | null>(null);
  const deleteMutation = useDeleteEvalSet(corpus.id);

  if (selectedSetId) {
    return (
      <EvalSetDetailPage
        corpus={corpus}
        evalSetId={selectedSetId}
        onBack={() => setSelectedSetId(null)}
      />
    );
  }

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
        Ошибка загрузки наборов оценки
      </div>
    );
  }

  const sets = data?.items ?? [];

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
          <h2 className="text-lg font-semibold">Оценка качества</h2>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Создать набор
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {sets.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет наборов оценки
          </div>
        ) : (
          <div className="space-y-2">
            {sets.map((s) => (
              <div
                key={s.id}
                className="flex items-center justify-between rounded-lg border border-border p-3"
              >
                <button
                  onClick={() => setSelectedSetId(s.id)}
                  className="flex flex-1 items-center gap-3 text-left"
                >
                  <ClipboardList className="h-5 w-5 text-muted-foreground" />
                  <div>
                    <div className="text-sm font-medium">{s.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {new Date(s.created_at).toLocaleDateString()}
                    </div>
                  </div>
                </button>
                <button
                  onClick={async () => {
                    try {
                      await deleteMutation.mutateAsync(s.id);
                    } catch {
                      // toast via global onError
                    }
                  }}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateEvalSetModal corpusId={corpus.id} onClose={() => setShowCreate(false)} />
      )}
    </div>
  );
}

function CreateEvalSetModal({
  corpusId,
  onClose,
}: {
  corpusId: string;
  onClose: () => void;
}) {
  const createMutation = useCreateEvalSet(corpusId);
  const [name, setName] = useState("");
  const [questions, setQuestions] = useState<string[]>([""]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const items = questions
      .filter((q) => q.trim())
      .map((q) => ({ question: q, expected_doc_ids: [], expected_answer: null }));
    try {
      await createMutation.mutateAsync({
        name,
        items,
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
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Новый набор оценки</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Название</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="Базовый набор"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Вопросы</label>
            {questions.map((q, i) => (
              <div key={i} className="mb-2 flex gap-2">
                <input
                  type="text"
                  value={q}
                  onChange={(e) => {
                    const next = [...questions];
                    next[i] = e.target.value;
                    setQuestions(next);
                  }}
                  className="flex-1 rounded-md border border-border px-3 py-2 text-sm"
                  placeholder={`Вопрос ${i + 1}`}
                />
                {questions.length > 1 && (
                  <button
                    type="button"
                    onClick={() => setQuestions(questions.filter((_, idx) => idx !== i))}
                    className="rounded-md p-2 text-muted-foreground hover:bg-accent"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              onClick={() => setQuestions([...questions, ""])}
              className="text-sm text-primary hover:underline"
            >
              + Добавить вопрос
            </button>
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

function EvalSetDetailPage({
  corpus: _corpus,
  evalSetId,
  onBack,
}: {
  corpus: CorpusResponse;
  evalSetId: string;
  onBack: () => void;
}) {
  const { data, isLoading, error } = useEvalSet(evalSetId);
  const createItemMutation = useCreateEvalItem(evalSetId);
  const deleteItemMutation = useDeleteEvalItem(evalSetId);
  const [showAddQuestion, setShowAddQuestion] = useState(false);
  const [newQuestion, setNewQuestion] = useState("");

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Наборы
          </button>
          <span className="text-muted-foreground">/</span>
          <h2 className="text-lg font-semibold">{data?.name ?? "Набор"}</h2>
        </div>
        <button
          onClick={() => setShowAddQuestion(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Вопрос
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        ) : error ? (
          <div className="text-destructive">Ошибка загрузки</div>
        ) : (
          <div className="space-y-2">
            {data?.items.map((item) => (
              <div
                key={item.id}
                className="flex items-start justify-between rounded-lg border border-border p-3"
              >
                <div className="flex-1">
                  <div className="text-sm">{item.question}</div>
                  {item.expected_answer && (
                    <div className="mt-1 text-xs text-muted-foreground">
                      Ожидаемый ответ: {item.expected_answer}
                    </div>
                  )}
                </div>
                <button
                  onClick={async () => {
                    try {
                      await deleteItemMutation.mutateAsync(item.id);
                    } catch {
                      // toast
                    }
                  }}
                  className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
            {data?.items.length === 0 && (
              <div className="flex h-full items-center justify-center text-muted-foreground">
                Нет вопросов
              </div>
            )}
          </div>
        )}
      </div>

      {showAddQuestion && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setShowAddQuestion(false)}
        >
          <div
            className="w-full max-w-md rounded-lg border border-border bg-background p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-3 text-lg font-semibold">Новый вопрос</h3>
            <textarea
              value={newQuestion}
              onChange={(e) => setNewQuestion(e.target.value)}
              className="mb-3 h-24 w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="Введите вопрос..."
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowAddQuestion(false)}
                className="rounded-md px-4 py-2 text-sm text-muted-foreground hover:bg-accent"
              >
                Отмена
              </button>
              <button
                onClick={async () => {
                  try {
                    await createItemMutation.mutateAsync({
                      question: newQuestion,
                      expected_doc_ids: [],
                      expected_answer: null,
                    });
                    setNewQuestion("");
                    setShowAddQuestion(false);
                  } catch {
                    // toast
                  }
                }}
                disabled={!newQuestion.trim() || createItemMutation.isPending}
                className="flex items-center gap-1 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
              >
                {createItemMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Добавить
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
