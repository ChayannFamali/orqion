import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useRagSettings, useUpdateRagSettings } from "../hooks/useRagSettings";

/**
 * T-506: общие настройки. Пока одна вкладка — «Поиск по документам»;
 * будущие вкладки темы/языка добавятся сюда же.
 *
 * Видна всем; право на изменение определяется по `manage_corpora`
 * (без него поля только для чтения). Отдельный раздел, не смешивается
 * с диагностикой окружения.
 */
export function SettingsPage({ capabilities }: { capabilities: string[] }) {
  const canManage = capabilities.includes("*") || capabilities.includes("manage_corpora");

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-3xl space-y-4">
        <div>
          <h2 className="text-xl font-bold">Настройки</h2>
        </div>

        <div className="border-b border-border">
          <button
            type="button"
            className="border-b-2 border-primary px-1 pb-2 text-sm font-medium text-foreground"
            data-testid="settings-tab-search"
          >
            Поиск по документам
          </button>
        </div>

        <RagSearchSettings canManage={canManage} />
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
