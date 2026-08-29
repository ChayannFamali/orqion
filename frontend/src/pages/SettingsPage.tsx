import { useEffect, useState } from "react";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useRagSettings, useUpdateRagSettings } from "../hooks/useRagSettings";
import {
  useCreatePromptTemplate,
  useDeletePromptTemplate,
  usePromptTemplates,
  useUpdatePromptTemplate,
} from "../hooks/usePromptTemplates";

/**
 * T-506/T-507: общие настройки.
 *
 * Вкладки:
 * - «Поиск по документам» (Т-506) — видна всем; право на изменение —
 *   `manage_corpora` (без него поля только для чтения).
 * - «Шаблоны промптов» (Т-507) — видна только со способностью
 *   `custom_prompts`; шаблоны личные, CRUD только у владельца.
 *
 * Отдельный раздел, не смешивается с диагностикой окружения.
 */
export function SettingsPage({ capabilities }: { capabilities: string[] }) {
  const canManage = capabilities.includes("*") || capabilities.includes("manage_corpora");
  const canPrompts = capabilities.includes("*") || capabilities.includes("custom_prompts");
  const [tab, setTab] = useState<"search" | "prompts">("search");

  const tabClass = (active: boolean) =>
    "border-b-2 px-1 pb-2 text-sm font-medium " +
    (active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground");

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-3xl space-y-4">
        <div>
          <h2 className="text-xl font-bold">Настройки</h2>
        </div>

        <div className="flex gap-4 border-b border-border">
          <button
            type="button"
            className={tabClass(tab === "search")}
            onClick={() => setTab("search")}
            data-testid="settings-tab-search"
          >
            Поиск по документам
          </button>
          {canPrompts && (
            <button
              type="button"
              className={tabClass(tab === "prompts")}
              onClick={() => setTab("prompts")}
              data-testid="settings-tab-prompts"
            >
              Шаблоны промптов
            </button>
          )}
        </div>

        {tab === "search" ? (
          <RagSearchSettings canManage={canManage} />
        ) : (
          <PromptTemplatesSettings />
        )}
      </div>
    </div>
  );
}

function RagSearchSettings({ canManage }: { canManage: boolean }) {
  const { data, isLoading, isError } = useRagSettings();
  const updateMutation = useUpdateRagSettings();

  const [threshold, setThreshold] = useState("");
  const [maxFragments, setMaxFragments] = useState("");

  useEffect(() => {
    if (data) {
      setThreshold(String(data.relevance_threshold));
      setMaxFragments(String(data.max_fragments));
    }
  }, [data]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>Загрузка настроек…</span>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        Не удалось загрузить настройки поиска.
      </div>
    );
  }

  const dirty =
    Number(threshold) !== data.relevance_threshold ||
    Number(maxFragments) !== data.max_fragments;

  const handleSave = () => {
    const t = Number(threshold);
    const m = Number(maxFragments);
    if (!Number.isInteger(t) || t < 0 || t > 100) {
      toast.error("Порог релевантности — целое число от 0 до 100");
      return;
    }
    if (!Number.isInteger(m) || m < 1 || m > 8) {
      toast.error("Максимум фрагментов — целое число от 1 до 8");
      return;
    }
    updateMutation.mutate(
      { relevance_threshold: t, max_fragments: m },
      {
        onSuccess: () => toast.success("Настройки поиска сохранены"),
        onError: () => toast.error("Не удалось сохранить настройки поиска"),
      },
    );
  };

  const inputClass =
    "w-24 rounded-md border border-border bg-background px-3 py-2 text-sm " +
    "focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-60";

  return (
    <div className="space-y-6 rounded-lg border border-border bg-card p-4">
      <div className="space-y-1">
        <label htmlFor="rag-threshold" className="text-sm font-medium">
          Порог релевантности после реранкинга
        </label>
        <p className="text-xs text-muted-foreground">
          Фрагменты с оценкой ниже порога не попадают в контекст. 0 — фильтр
          выключен. Ориентиры: низкий ~30, средний ~50, высокий ~70.
        </p>
        <div className="flex items-center gap-2 pt-1">
          <input
            id="rag-threshold"
            type="number"
            min={0}
            max={100}
            step={1}
            value={threshold}
            disabled={!canManage}
            onChange={(e) => setThreshold(e.target.value)}
            className={inputClass}
            data-testid="rag-threshold-input"
          />
          <span className="text-sm text-muted-foreground">%</span>
        </div>
      </div>

      <div className="space-y-1">
        <label htmlFor="rag-max-fragments" className="text-sm font-medium">
          Максимум фрагментов контекста
        </label>
        <p className="text-xs text-muted-foreground">
          Сколько фрагментов передавать модели (от 1 до 8). Ограничение сверху
          поверх отбора реранкером; на бюджет токенов влияет отдельно.
        </p>
        <div className="flex items-center gap-2 pt-1">
          <input
            id="rag-max-fragments"
            type="number"
            min={1}
            max={8}
            step={1}
            value={maxFragments}
            disabled={!canManage}
            onChange={(e) => setMaxFragments(e.target.value)}
            className={inputClass}
            data-testid="rag-max-fragments-input"
          />
          <span className="text-sm text-muted-foreground">шт.</span>
        </div>
      </div>

      {canManage ? (
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || updateMutation.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          data-testid="rag-settings-save"
        >
          {updateMutation.isPending ? "Сохранение…" : "Сохранить"}
        </button>
      ) : (
        <p className="text-xs text-muted-foreground" data-testid="rag-settings-readonly">
          Изменение настроек доступно с правом управления корпусами.
        </p>
      )}
    </div>
  );
}

const TITLE_LIMIT = 200;

function PromptTemplatesSettings() {
  const { data, isLoading, isError } = usePromptTemplates();
  const createMutation = useCreatePromptTemplate();
  const updateMutation = useUpdatePromptTemplate();
  const deleteMutation = useDeletePromptTemplate();

  // null — форма закрыта; строка — новый шаблон; иначе редактируемый id.
  const [formId, setFormId] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  const openCreate = () => {
    setFormId(null);
    setFormOpen(true);
    setTitle("");
    setBody("");
  };

  const openEdit = (id: string, t: string, b: string) => {
    setFormId(id);
    setFormOpen(true);
    setTitle(t);
    setBody(b);
  };

  const closeForm = () => {
    setFormOpen(false);
    setFormId(null);
    setTitle("");
    setBody("");
  };

  const handleSave = () => {
    const trimmedTitle = title.trim();
    const trimmedBody = body;
    if (!trimmedTitle) {
      toast.error("Название шаблона не может быть пустым");
      return;
    }
    if (trimmedTitle.length > TITLE_LIMIT) {
      toast.error(`Название шаблона — не более ${TITLE_LIMIT} символов`);
      return;
    }
    if (!trimmedBody.trim()) {
      toast.error("Текст шаблона не может быть пустым");
      return;
    }
    const payload = { title: trimmedTitle, body: trimmedBody };
    if (formId === null) {
      createMutation.mutate(payload, {
        onSuccess: () => {
          toast.success("Шаблон сохранён");
          closeForm();
        },
        onError: () => toast.error("Не удалось сохранить шаблон"),
      });
    } else {
      updateMutation.mutate(
        { id: formId, body: payload },
        {
          onSuccess: () => {
            toast.success("Шаблон сохранён");
            closeForm();
          },
          onError: () => toast.error("Не удалось сохранить шаблон"),
        },
      );
    }
  };

  const handleDelete = (id: string, t: string) => {
    if (!window.confirm(`Удалить шаблон «${t}»?`)) return;
    deleteMutation.mutate(id, {
      onSuccess: () => toast.success("Шаблон удалён"),
      onError: () => toast.error("Не удалось удалить шаблон"),
    });
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span>Загрузка шаблонов…</span>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
        Не удалось загрузить шаблоны промптов.
      </div>
    );
  }

  const templates = data.templates;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        Личные текстовые шаблоны: готовые формулировки вопросов и системные
        промпты. Применяются в чате кнопкой выбора у поля ввода.
      </p>

      {!formOpen && (
        <button
          type="button"
          onClick={openCreate}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          data-testid="prompt-template-create"
        >
          <Plus className="h-4 w-4" />
          Новый шаблон
        </button>
      )}

      {formOpen && (
        <div className="space-y-3 rounded-lg border border-border bg-card p-4">
          <div className="space-y-1">
            <label htmlFor="prompt-template-title" className="text-sm font-medium">
              Название
            </label>
            <input
              id="prompt-template-title"
              type="text"
              value={title}
              maxLength={TITLE_LIMIT}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              data-testid="prompt-template-title"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="prompt-template-body" className="text-sm font-medium">
              Текст шаблона
            </label>
            <textarea
              id="prompt-template-body"
              value={body}
              rows={6}
              onChange={(e) => setBody(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
              data-testid="prompt-template-body"
            />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={createMutation.isPending || updateMutation.isPending}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              data-testid="prompt-template-save"
            >
              Сохранить
            </button>
            <button
              type="button"
              onClick={closeForm}
              className="rounded-md border border-border px-4 py-2 text-sm text-muted-foreground hover:bg-accent"
              data-testid="prompt-template-cancel"
            >
              Отмена
            </button>
          </div>
        </div>
      )}

      {templates.length === 0 && !formOpen ? (
        <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
          Шаблонов пока нет.
        </div>
      ) : (
        <ul className="space-y-2">
          {templates.map((t) => (
            <li
              key={t.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-border bg-card p-3"
              data-testid="prompt-template-item"
            >
              <div className="min-w-0">
                <div className="text-sm font-medium">{t.title}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {t.body.split("\n")[0]}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <button
                  type="button"
                  onClick={() => openEdit(t.id, t.title, t.body)}
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                  title="Изменить"
                  data-testid="prompt-template-edit"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(t.id, t.title)}
                  className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-destructive"
                  title="Удалить"
                  data-testid="prompt-template-delete"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
