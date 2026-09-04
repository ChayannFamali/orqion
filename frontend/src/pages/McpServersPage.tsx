import { useState } from "react";
import { Loader2, Plus, X, Key } from "lucide-react";
import {
  useCreateMcpServer,
  useDeleteMcpServer,
  useMcpServers,
  useUpdateMcpServer,
} from "../hooks/useMcpServers";
import type { McpServerResponse } from "../api/types";

/**
 * Админский реестр серверов внешних инструментов (Т-503).
 * Видимость раздела — по способности ``manage_mcp_servers`` в реестре
 * навигации; право проверяется и на сервере (без права — 404).
 */
export function McpServersPage() {
  const { data, isLoading, error } = useMcpServers();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingServer, setEditingServer] = useState<string | null>(null);
  const [deletingServer, setDeletingServer] = useState<McpServerResponse | null>(null);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="mcp-servers-loading">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="flex h-full items-center justify-center text-destructive"
        data-testid="mcp-servers-error"
      >
        Ошибка загрузки серверов инструментов
      </div>
    );
  }

  const servers = data?.servers ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Серверы инструментов</h2>
        <button
          onClick={() => setShowCreateForm(true)}
          data-testid="mcp-servers-add"
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Добавить
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {servers.length === 0 ? (
          <div
            className="flex h-full items-center justify-center text-muted-foreground"
            data-testid="mcp-servers-empty"
          >
            Нет серверов инструментов
          </div>
        ) : (
          <div className="space-y-3">
            {servers.map((server) => (
              <McpServerCard
                key={server.id}
                server={server}
                onEdit={() => setEditingServer(server.id)}
                onDelete={() => setDeletingServer(server)}
              />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && <CreateMcpServerModal onClose={() => setShowCreateForm(false)} />}
      {editingServer && (
        <EditMcpServerModal
          server={servers.find((s) => s.id === editingServer)!}
          onClose={() => setEditingServer(null)}
        />
      )}
      {deletingServer && (
        <DeleteMcpServerModal server={deletingServer} onClose={() => setDeletingServer(null)} />
      )}
    </div>
  );
}

function McpServerCard({
  server,
  onEdit,
  onDelete,
}: {
  server: McpServerResponse;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const updateMutation = useUpdateMcpServer();

  const handleToggle = () => {
    updateMutation.mutate({
      serverId: server.id,
      body: { enabled: !server.enabled },
    });
  };

  return (
    <div
      className="rounded-lg border border-border bg-background p-4"
      data-testid={`mcp-server-${server.name}`}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{server.name}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs ${
                server.enabled
                  ? "bg-success/15 text-success"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {server.enabled ? "включён" : "отключён"}
            </span>
          </div>
          <div className="mt-1 truncate text-sm text-muted-foreground">{server.url}</div>
          <div className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            <Key className="h-3 w-3" />
            {server.has_api_key ? "секрет задан" : "без секрета"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={handleToggle}
            disabled={updateMutation.isPending}
            data-testid={`mcp-server-toggle-${server.name}`}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            {server.enabled ? "Отключить" : "Включить"}
          </button>
          <button
            onClick={onEdit}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            Изменить
          </button>
          <button
            onClick={onDelete}
            className="rounded-md border border-destructive/40 px-3 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/10"
          >
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}

function CreateMcpServerModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateMcpServer();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({
      name,
      url,
      api_key: apiKey || null,
      enabled: true,
    });
    onClose();
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
          <h3 className="text-lg font-semibold">Новый сервер инструментов</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">Имя</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="mcp-server-create-name"
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="wiki"
              required
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Строчные латинские буквы, цифры, дефис и подчёркивание; начинается с
              буквы. Имя входит в названия инструментов сервера и не меняется после
              создания.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Адрес</label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              data-testid="mcp-server-create-url"
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="https://tools.example.com/mcp"
              required
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Только http:// или https:// с явным хостом.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Секрет (необязательно)</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="Токен доступа к серверу"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Секрет шифруется и не отображается после сохранения.
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

function EditMcpServerModal({
  server,
  onClose,
}: {
  server: McpServerResponse;
  onClose: () => void;
}) {
  const updateMutation = useUpdateMcpServer();
  const [url, setUrl] = useState(server.url);
  const [apiKey, setApiKey] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { url };
    if (apiKey) {
      body.api_key = apiKey;
    }
    await updateMutation.mutateAsync({ serverId: server.id, body });
    onClose();
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
          <h3 className="text-lg font-semibold">Изменить сервер инструментов</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">Имя</label>
            <input
              type="text"
              value={server.name}
              disabled
              className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Имя входит в названия инструментов сервера и не меняется.
            </p>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Адрес</label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              data-testid="mcp-server-edit-url"
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
            />
          </div>
          <div>
            <label className="mb-1 flex items-center gap-1 text-sm font-medium">
              <Key className="h-3 w-3" />
              Новый секрет (замена)
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="Оставьте пустым, чтобы не менять"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Текущий секрет не отображается. Введите новый для замены.
            </p>
          </div>
          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Сохранить
          </button>
        </form>
      </div>
    </div>
  );
}

function DeleteMcpServerModal({
  server,
  onClose,
}: {
  server: McpServerResponse;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteMcpServer();

  const handleConfirm = async () => {
    try {
      await deleteMutation.mutateAsync(server.id);
      onClose();
    } catch {
      // Ошибки удаления — через глобальный обработчик мутаций
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
          <h3 className="text-lg font-semibold">Удалить сервер инструментов</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <div className="space-y-3">
          <p className="text-sm">
            Удалить сервер <span className="font-medium">{server.name}</span> (
            {server.url})?
          </p>
          <p className="text-xs text-muted-foreground">
            Инструменты этого сервера перестанут быть доступны в агентных диалогах.
            Чтобы временно выключить сервер, используйте «Отключить».
          </p>
          <div className="flex gap-2">
            <button
              onClick={handleConfirm}
              disabled={deleteMutation.isPending}
              className="flex flex-1 items-center justify-center gap-2 rounded-md bg-destructive px-4 py-2 text-sm text-destructive-foreground transition-colors hover:bg-destructive/90"
            >
              {deleteMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Удалить
            </button>
            <button
              onClick={onClose}
              className="flex-1 rounded-md border border-border px-4 py-2 text-sm transition-colors hover:bg-accent"
            >
              Отмена
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
