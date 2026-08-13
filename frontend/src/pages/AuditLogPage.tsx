import { useState, useMemo } from "react";
import { Loader2, ChevronDown, ChevronRight, Filter } from "lucide-react";
import { useAuditLog, useAuditActions } from "../hooks/useAudit";
import type { AuditLogResponse } from "../api/types";

const PAGE_SIZE = 50;

export function AuditLogPage() {
  const [actionFilter, setActionFilter] = useState<string | undefined>(undefined);
  const [actorFilter, setActorFilter] = useState<string>("");
  const [startFilter, setStartFilter] = useState<string>("");
  const [endFilter, setEndFilter] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const params = useMemo(
    () => ({
      limit: PAGE_SIZE,
      offset,
      action: actionFilter || undefined,
      actor_user_id: actorFilter || undefined,
      start: startFilter || undefined,
      end: endFilter || undefined,
    }),
    [offset, actionFilter, actorFilter, startFilter, endFilter],
  );

  const { data, isLoading, error } = useAuditLog(params);
  const { data: actionsData } = useAuditActions();

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
        Ошибка загрузки журнала аудита
      </div>
    );
  }

  const entries = data?.entries ?? [];
  const total = data?.total ?? 0;
  const actions = actionsData?.actions ?? [];

  function applyFilters() {
    setOffset(0);
  }

  function clearFilters() {
    setActionFilter(undefined);
    setActorFilter("");
    setStartFilter("");
    setEndFilter("");
    setOffset(0);
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Журнал аудита</h2>
        <p className="text-sm text-muted-foreground">Всего записей: {total}</p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <Filter className="h-4 w-4 text-muted-foreground" />
        <select
          value={actionFilter ?? ""}
          onChange={(e) => setActionFilter(e.target.value || undefined)}
          className="rounded border border-border bg-background px-2 py-1 text-sm"
        >
          <option value="">Все действия</option>
          {actions.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="ID пользователя"
          value={actorFilter}
          onChange={(e) => setActorFilter(e.target.value)}
          className="w-40 rounded border border-border bg-background px-2 py-1 text-sm"
        />
        <input
          type="date"
          value={startFilter}
          onChange={(e) => setStartFilter(e.target.value)}
          className="rounded border border-border bg-background px-2 py-1 text-sm"
        />
        <span className="text-sm text-muted-foreground">—</span>
        <input
          type="date"
          value={endFilter}
          onChange={(e) => setEndFilter(e.target.value)}
          className="rounded border border-border bg-background px-2 py-1 text-sm"
        />
        <button
          onClick={applyFilters}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground hover:opacity-90"
        >
          Применить
        </button>
        <button
          onClick={clearFilters}
          className="rounded border border-border px-3 py-1 text-sm hover:bg-accent"
        >
          Сбросить
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {entries.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет записей
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="sticky top-0 border-b border-border bg-background">
              <tr>
                <th className="px-2 py-2 text-left"></th>
                <th className="px-2 py-2 text-left">Время</th>
                <th className="px-2 py-2 text-left">Действие</th>
                <th className="px-2 py-2 text-left">Объект</th>
                <th className="px-2 py-2 text-left">Пользователь</th>
                <th className="px-2 py-2 text-left">meta</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <AuditRow
                  key={entry.id}
                  entry={entry}
                  expanded={expandedId === entry.id}
                  onToggle={() =>
                    setExpandedId(expandedId === entry.id ? null : entry.id)
                  }
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between border-t border-border px-4 py-2 text-sm">
          <span className="text-muted-foreground">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} из {total}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              Назад
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= total}
              className="rounded border border-border px-3 py-1 disabled:opacity-40"
            >
              Вперёд
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AuditRow({
  entry,
  expanded,
  onToggle,
}: {
  entry: AuditLogResponse;
  expanded: boolean;
  onToggle: () => void;
}) {
  const metaStr = JSON.stringify(entry.meta, null, 2);
  const metaPreview = metaStr.length > 60 ? metaStr.slice(0, 60) + "…" : metaStr;

  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-border transition-colors hover:bg-accent"
      >
        <td className="px-2 py-2">
          {expanded ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
        </td>
        <td className="px-2 py-2 whitespace-nowrap">
          {new Date(entry.ts).toLocaleString()}
        </td>
        <td className="px-2 py-2 font-mono">{entry.action}</td>
        <td className="px-2 py-2">
          {entry.object_type}
          {entry.object_id ? `:${entry.object_id.slice(0, 8)}` : ""}
        </td>
        <td className="px-2 py-2 font-mono text-muted-foreground">
          {entry.actor_user_id.slice(0, 8)}
        </td>
        <td className="px-2 py-2 font-mono text-muted-foreground">{metaPreview}</td>
      </tr>
      {expanded && (
        <tr className="border-b border-border bg-accent/30">
          <td colSpan={6} className="px-4 py-3">
            <pre className="overflow-x-auto text-xs">{metaStr}</pre>
          </td>
        </tr>
      )}
    </>
  );
}
