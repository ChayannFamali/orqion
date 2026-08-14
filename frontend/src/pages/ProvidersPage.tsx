import { useState } from "react";
import { Loader2, Plus, X, Zap, Key, Activity, CheckCircle, XCircle, Settings2 } from "lucide-react";
import { useCreateModel, useCreateProvider, useProbeProvider, useProviders, useUpdateModel, useUpdateProvider } from "../hooks/useProviders";
import type { ModelResponse, ProbeResult, ProviderResponse } from "../api/types";

export function ProvidersPage() {
  const { data, isLoading, error } = useProviders();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [probeResults, setProbeResults] = useState<Record<string, ProbeResult>>({});
  const [creatingModelFor, setCreatingModelFor] = useState<string | null>(null);
  const [editingModel, setEditingModel] = useState<ModelResponse | null>(null);

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
        Ошибка загрузки провайдеров
      </div>
    );
  }

  const providers = data?.providers ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Провайдеры</h2>
        <button
          onClick={() => setShowCreateForm(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Добавить
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {providers.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет провайдеров
          </div>
        ) : (
          <div className="space-y-3">
            {providers.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                probeResult={probeResults[provider.id]}
                onProbeResult={(result) => setProbeResults((prev) => ({ ...prev, [provider.id]: result }))}
                onEdit={() => setEditingProvider(provider.id)}
                onAddModel={() => setCreatingModelFor(provider.id)}
                onEditModel={(model) => setEditingModel(model)}
              />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && (
        <CreateProviderModal onClose={() => setShowCreateForm(false)} />
      )}
      {editingProvider && (
        <EditProviderModal
          provider={providers.find((p) => p.id === editingProvider)!}
          onClose={() => setEditingProvider(null)}
        />
      )}
      {creatingModelFor && (
        <CreateModelModal
          providerId={creatingModelFor}
          onClose={() => setCreatingModelFor(null)}
        />
      )}
      {editingModel && (
        <EditModelModal
          model={editingModel}
          onClose={() => setEditingModel(null)}
        />
      )}
    </div>
  );
}

function ProviderCard({
  provider,
  probeResult,
  onProbeResult,
  onEdit,
  onAddModel,
  onEditModel,
}: {
  provider: ProviderResponse;
  probeResult?: ProbeResult;
  onProbeResult: (result: ProbeResult) => void;
  onEdit: () => void;
  onAddModel: () => void;
  onEditModel: (model: ModelResponse) => void;
}) {
  const probeMutation = useProbeProvider();
  const updateMutation = useUpdateProvider();
  const updateModelMutation = useUpdateModel();

  const handleProbe = async () => {
    try {
      const result = await probeMutation.mutateAsync({ providerId: provider.id });
      onProbeResult(result);
    } catch {
      onProbeResult({ available_models: [], supports_streaming: false, max_parallel: 0, model_statuses: [], error: "probe failed" });
    }
  };

  const handleToggle = async () => {
    await updateMutation.mutateAsync({
      providerId: provider.id,
      body: { enabled: !provider.enabled },
    });
  };

  const caps = provider.capabilities as Record<string, unknown>;
  const lastProbe = caps.last_probe_at as string | undefined;

  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{provider.kind}</span>
            <span
              className={
                "rounded px-1.5 py-0.5 text-xs " +
                (provider.enabled
                  ? "bg-green-500/10 text-green-600"
                  : "bg-muted text-muted-foreground")
              }
            >
              {provider.enabled ? "включён" : "отключён"}
            </span>
          </div>
          <div className="text-sm text-muted-foreground">{provider.base_url}</div>
          {lastProbe && (
            <div className="text-xs text-muted-foreground">
              Последний probe: {new Date(lastProbe).toLocaleString()}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleProbe}
            disabled={probeMutation.isPending}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent"
          >
            {probeMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <Zap className="h-3 w-3" />
            )}
            Проверить
          </button>
          <button
            onClick={handleToggle}
            disabled={updateMutation.isPending}
            className="rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent"
          >
            {provider.enabled ? "Отключить" : "Включить"}
          </button>
          <button
            onClick={onEdit}
            className="rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent"
          >
            Изменить
          </button>
        </div>
      </div>

      {provider.models.length > 0 && (
        <div className="mt-3 space-y-1">
          <div className="flex items-center justify-between">
            <div className="text-xs font-medium text-muted-foreground">Модели:</div>
            <button
              onClick={onAddModel}
              className="flex items-center gap-0.5 text-xs text-primary hover:underline"
            >
              <Plus className="h-3 w-3" />
              Добавить модель
            </button>
          </div>
          <ul className="space-y-0.5">
            {provider.models.map((model) => {
              const status = probeResult?.model_statuses?.find((ms) => ms.model_id === model.id);
              return (
                <li key={model.id} className="flex items-center gap-2 text-xs">
                  {status ? (
                    status.status === "available" ? (
                      <CheckCircle className="h-3 w-3 text-green-500" />
                    ) : (
                      <XCircle className="h-3 w-3 text-destructive" />
                    )
                  ) : (
                    <Activity className="h-3 w-3 text-muted-foreground" />
                  )}
                  <span className={model.enabled ? "" : "text-muted-foreground line-through"}>
                    {model.alias}
                  </span>
                  <span className="text-muted-foreground">→ {model.upstream_name}</span>
                  {status?.status === "unavailable" && (
                    <span className="text-destructive">(недоступна)</span>
                  )}
                  <button
                    onClick={() => updateModelMutation.mutateAsync({
                      modelId: model.id,
                      body: { enabled: !model.enabled },
                    })}
                    disabled={updateModelMutation.isPending}
                    className="ml-auto rounded px-1 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    {model.enabled ? "выкл" : "вкл"}
                  </button>
                  <button
                    onClick={() => onEditModel(model)}
                    className="rounded px-1 py-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  >
                    <Settings2 className="h-3 w-3" />
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {provider.models.length === 0 && (
        <div className="mt-3">
          <button
            onClick={onAddModel}
            className="flex items-center gap-0.5 text-xs text-primary hover:underline"
          >
            <Plus className="h-3 w-3" />
            Добавить модель
          </button>
        </div>
      )}

      {probeResult && (
        <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs">
          <div className="font-medium text-muted-foreground">Результат probe:</div>
          {probeResult.error ? (
            <div className="text-destructive">Ошибка: {probeResult.error}</div>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">Стриминг:</span>
                {probeResult.supports_streaming ? (
                  <CheckCircle className="h-3 w-3 text-green-500" />
                ) : (
                  <XCircle className="h-3 w-3 text-destructive" />
                )}
              </div>
              <div>
                <span className="text-muted-foreground">Параллелизм:</span>{" "}
                {probeResult.max_parallel}
              </div>
              <div>
                <span className="text-muted-foreground">Доступные модели:</span>{" "}
                {probeResult.available_models.join(", ") || "нет"}
              </div>
              {probeResult.observed_context && (
                <div>
                  <span className="text-muted-foreground">Контекст (измеренный):</span>{" "}
                  {Object.entries(probeResult.observed_context).map(([alias, ctx]) => (
                    <span key={alias}>
                      {alias}={ctx ?? "—"}{" "}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function CreateProviderModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateProvider();
  const [kind, setKind] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({
      kind,
      base_url: baseUrl,
      api_key: apiKey || null,
      enabled: true,
    });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Новый провайдер</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">Тип (kind)</label>
            <input
              type="text"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="openai"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Base URL</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="http://127.0.0.1:1234/v1"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">API ключ (необязательно)</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="sk-..."
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Ключ шифруется и не отображается после сохранения.
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

function EditProviderModal({
  provider,
  onClose,
}: {
  provider: ProviderResponse;
  onClose: () => void;
}) {
  const updateMutation = useUpdateProvider();
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [apiKey, setApiKey] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { base_url: baseUrl };
    if (apiKey) {
      body.api_key = apiKey;
    }
    await updateMutation.mutateAsync({ providerId: provider.id, body });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Изменить провайдер</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">Base URL</label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
            />
          </div>
          <div>
            <label className="mb-1 flex items-center gap-1 text-sm font-medium">
              <Key className="h-3 w-3" />
              Новый API ключ (ротация)
            </label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="Оставьте пустым, чтобы не менять"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Текущий ключ не отображается. Введите новый для ротации.
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

function CreateModelModal({
  providerId,
  onClose,
}: {
  providerId: string;
  onClose: () => void;
}) {
  const createMutation = useCreateModel();
  const [alias, setAlias] = useState("");
  const [upstreamName, setUpstreamName] = useState("");
  const [locality, setLocality] = useState("local");
  const [maxInputTokens, setMaxInputTokens] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("");
  const [supportsReasoning, setSupportsReasoning] = useState(false);
  const [costIn, setCostIn] = useState("");
  const [costOut, setCostOut] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync({
        providerId,
        body: {
          alias,
          upstream_name: upstreamName,
          locality,
          max_input_tokens: maxInputTokens ? parseInt(maxInputTokens) : null,
          max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) : null,
          supports_reasoning: supportsReasoning,
          cost_in: costIn ? parseFloat(costIn) : null,
          cost_out: costOut ? parseFloat(costOut) : null,
          enabled: true,
        },
      });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Новая модель</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <FormField label="Алиас" value={alias} onChange={setAlias} required placeholder="my-model" />
          <FormField label="Upstream name" value={upstreamName} onChange={setUpstreamName} required placeholder="qwen2.5-coder-7b" />
          <FormField label="Локальность" value={locality} onChange={setLocality} placeholder="local" />
          <FormField label="Max input tokens" value={maxInputTokens} onChange={setMaxInputTokens} type="number" placeholder="4096" />
          <FormField label="Max output tokens" value={maxOutputTokens} onChange={setMaxOutputTokens} type="number" placeholder="2048" />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={supportsReasoning}
              onChange={(e) => setSupportsReasoning(e.target.checked)}
            />
            Supports reasoning
          </label>
          <FormField label="Cost in (per 1K tokens)" value={costIn} onChange={setCostIn} type="number" placeholder="0.0" />
          <FormField label="Cost out (per 1K tokens)" value={costOut} onChange={setCostOut} type="number" placeholder="0.0" />
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

function EditModelModal({
  model,
  onClose,
}: {
  model: ModelResponse;
  onClose: () => void;
}) {
  const updateMutation = useUpdateModel();
  const [alias, setAlias] = useState(model.alias);
  const [upstreamName, setUpstreamName] = useState(model.upstream_name);
  const [locality, setLocality] = useState(model.locality);
  const [maxInputTokens, setMaxInputTokens] = useState(
    model.max_input_tokens?.toString() ?? "",
  );
  const [maxOutputTokens, setMaxOutputTokens] = useState(
    model.max_output_tokens?.toString() ?? "",
  );
  const [supportsReasoning, setSupportsReasoning] = useState(model.supports_reasoning);
  const [costIn, setCostIn] = useState(model.cost_in?.toString() ?? "");
  const [costOut, setCostOut] = useState(model.cost_out?.toString() ?? "");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await updateMutation.mutateAsync({
        modelId: model.id,
        body: {
          alias,
          upstream_name: upstreamName,
          locality,
          max_input_tokens: maxInputTokens ? parseInt(maxInputTokens) : null,
          max_output_tokens: maxOutputTokens ? parseInt(maxOutputTokens) : null,
          supports_reasoning: supportsReasoning,
          cost_in: costIn ? parseFloat(costIn) : null,
          cost_out: costOut ? parseFloat(costOut) : null,
        },
      });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Изменить модель</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <FormField label="Алиас" value={alias} onChange={setAlias} required />
          <FormField label="Upstream name" value={upstreamName} onChange={setUpstreamName} required />
          <FormField label="Локальность" value={locality} onChange={setLocality} />
          <FormField label="Max input tokens" value={maxInputTokens} onChange={setMaxInputTokens} type="number" />
          <FormField label="Max output tokens" value={maxOutputTokens} onChange={setMaxOutputTokens} type="number" />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={supportsReasoning}
              onChange={(e) => setSupportsReasoning(e.target.checked)}
            />
            Supports reasoning
          </label>
          <FormField label="Cost in (per 1K tokens)" value={costIn} onChange={setCostIn} type="number" />
          <FormField label="Cost out (per 1K tokens)" value={costOut} onChange={setCostOut} type="number" />
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

function FormField({
  label,
  value,
  onChange,
  required,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-border px-3 py-2 text-sm"
        required={required}
        placeholder={placeholder}
      />
    </div>
  );
}
