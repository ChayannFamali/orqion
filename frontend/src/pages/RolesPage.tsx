import { useState } from "react";
import { Loader2, Plus, X, Shield, AlertTriangle } from "lucide-react";
import { useCreateRole, useRoles, useUpdateRole } from "../hooks/useRoles";
import type { RoleResponse } from "../api/types";

const KNOWN_CAPABILITIES = [
  "chat",
  "upload",
  "custom_prompts",
  "manage_corpora",
  "share",
  "view_analytics",
  "manage_routing",
  "view_traces",
  "manage_providers",
  "manage_roles",
  "view_diagnostics",
];

const REASONING_OPTIONS = ["off", "optional", "on"] as const;

interface PolicyForm {
  models: string;
  max_input_tokens: string;
  max_output_tokens: string;
  reasoning: string;
  budget_tokens_month: string;
  budget_cost_month: string;
  rpm: string;
  tpm: string;
  corpora: string;
  capabilities: string[];
}

function policyToForm(policy: Record<string, unknown>): PolicyForm {
  const budget = policy.budget as Record<string, number> | null | undefined;
  return {
    models: Array.isArray(policy.models) ? (policy.models as string[]).join(", ") : "",
    max_input_tokens: policy.max_input_tokens?.toString() ?? "",
    max_output_tokens: policy.max_output_tokens?.toString() ?? "",
    reasoning: (policy.reasoning as string) ?? "off",
    budget_tokens_month: budget?.tokens_month?.toString() ?? "",
    budget_cost_month: budget?.cost_month?.toString() ?? "",
    rpm: policy.rpm?.toString() ?? "",
    tpm: policy.tpm?.toString() ?? "",
    corpora: Array.isArray(policy.corpora) ? (policy.corpora as string[]).join(", ") : "",
    capabilities: Array.isArray(policy.capabilities)
      ? (policy.capabilities as string[])
      : [],
  };
}

function formToPolicy(form: PolicyForm): Record<string, unknown> {
  const policy: Record<string, unknown> = {
    models: form.models.split(",").map((s) => s.trim()).filter(Boolean),
    reasoning: form.reasoning,
    corpora: form.corpora.split(",").map((s) => s.trim()).filter(Boolean),
    capabilities: form.capabilities,
  };
  if (form.max_input_tokens) policy.max_input_tokens = parseInt(form.max_input_tokens);
  else policy.max_input_tokens = null;
  if (form.max_output_tokens) policy.max_output_tokens = parseInt(form.max_output_tokens);
  else policy.max_output_tokens = null;
  if (form.rpm) policy.rpm = parseInt(form.rpm);
  else policy.rpm = null;
  if (form.tpm) policy.tpm = parseInt(form.tpm);
  else policy.tpm = null;
  if (form.budget_tokens_month || form.budget_cost_month) {
    policy.budget = {
      tokens_month: form.budget_tokens_month ? parseInt(form.budget_tokens_month) : 0,
      cost_month: form.budget_cost_month ? parseInt(form.budget_cost_month) : 0,
    };
  } else {
    policy.budget = null;
  }
  return policy;
}

export function RolesPage() {
  const { data, isLoading, error } = useRoles();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingRole, setEditingRole] = useState<RoleResponse | null>(null);

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
        Ошибка загрузки ролей
      </div>
    );
  }

  const roles = data?.roles ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Роли</h2>
        <button
          onClick={() => setShowCreateForm(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Добавить
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {roles.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет ролей
          </div>
        ) : (
          <div className="space-y-3">
            {roles.map((role) => (
              <RoleCard key={role.id} role={role} onEdit={() => setEditingRole(role)} />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && <CreateRoleModal onClose={() => setShowCreateForm(false)} />}
      {editingRole && (
        <EditRoleModal role={editingRole} onClose={() => setEditingRole(null)} />
      )}
    </div>
  );
}

function RoleCard({ role, onEdit }: { role: RoleResponse; onEdit: () => void }) {
  const caps = (role.policy.capabilities as string[]) ?? [];
  const isWildcard = caps.includes("*");

  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            {isWildcard && <Shield className="h-4 w-4 text-primary" />}
            <span className="font-medium">{role.name}</span>
            <span
              className={
                "rounded px-1.5 py-0.5 text-xs " +
                (role.is_builtin
                  ? "bg-primary/10 text-primary"
                  : "bg-muted text-muted-foreground")
              }
            >
              {role.is_builtin ? "встроенная" : "кастомная"}
            </span>
          </div>
          <div className="text-sm text-muted-foreground">
            {isWildcard
              ? "Все права (admin)"
              : caps.length > 0
                ? caps.join(", ")
                : "без прав"}
          </div>
          <div className="text-xs text-muted-foreground">
            Модели: {(role.policy.models as string[])?.join(", ") ?? "—"} ·{" "}
            RPM: {(role.policy.rpm as number | null) ?? "∞"} ·{" "}
            TPM: {(role.policy.tpm as number | null) ?? "∞"}
          </div>
        </div>
        <button
          onClick={onEdit}
          className="rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent"
        >
          Изменить
        </button>
      </div>
    </div>
  );
}

function CreateRoleModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateRole();
  const [name, setName] = useState("");
  const [form, setForm] = useState<PolicyForm>({
    models: "local/*",
    max_input_tokens: "",
    max_output_tokens: "",
    reasoning: "off",
    budget_tokens_month: "",
    budget_cost_month: "",
    rpm: "",
    tpm: "",
    corpora: "public",
    capabilities: ["chat"],
  });
  const [rawJson, setRawJson] = useState("");
  const [useRaw, setUseRaw] = useState(false);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    let policy: Record<string, unknown>;

    if (useRaw) {
      try {
        policy = JSON.parse(rawJson);
        setJsonError(null);
      } catch (err) {
        setJsonError(err instanceof Error ? err.message : "Некорректный JSON");
        return;
      }
    } else {
      policy = formToPolicy(form);
    }

    try {
      await createMutation.mutateAsync({ name, policy });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Новая роль</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium">Имя роли</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="intern"
              required
            />
          </div>

          <div className="flex gap-2 border-b border-border pb-2">
            <button
              type="button"
              onClick={() => setUseRaw(false)}
              className={
                "rounded-md px-3 py-1 text-sm " +
                (!useRaw ? "bg-primary text-primary-foreground" : "text-muted-foreground")
              }
            >
              Форма
            </button>
            <button
              type="button"
              onClick={() => {
                setUseRaw(true);
                setRawJson(JSON.stringify(formToPolicy(form), null, 2));
              }}
              className={
                "rounded-md px-3 py-1 text-sm " +
                (useRaw ? "bg-primary text-primary-foreground" : "text-muted-foreground")
              }
            >
              JSON
            </button>
          </div>

          {useRaw ? (
            <div>
              <textarea
                value={rawJson}
                onChange={(e) => {
                  setRawJson(e.target.value);
                  setJsonError(null);
                }}
                onBlur={() => {
                  try {
                    JSON.parse(rawJson);
                    setJsonError(null);
                  } catch (err) {
                    setJsonError(err instanceof Error ? err.message : "Некорректный JSON");
                  }
                }}
                className="h-64 w-full rounded-md border border-border px-3 py-2 font-mono text-xs"
                placeholder='{"models": ["local/*"], ...}'
              />
              {jsonError && (
                <p className="mt-1 text-xs text-destructive">{jsonError}</p>
              )}
            </div>
          ) : (
            <PolicyFields form={form} setForm={setForm} />
          )}

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

function EditRoleModal({ role, onClose }: { role: RoleResponse; onClose: () => void }) {
  const updateMutation = useUpdateRole();
  const [form, setForm] = useState<PolicyForm>(policyToForm(role.policy));
  const [rawJson, setRawJson] = useState("");
  const [useRaw, setUseRaw] = useState(false);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [showLockoutWarning, setShowLockoutWarning] = useState(false);

  const isAdminRole = role.name === "admin" && role.is_builtin;
  const currentCaps = form.capabilities;
  const willLoseWildcard = isAdminRole && !currentCaps.includes("*");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    let policy: Record<string, unknown>;

    if (useRaw) {
      try {
        policy = JSON.parse(rawJson);
        setJsonError(null);
      } catch (err) {
        setJsonError(err instanceof Error ? err.message : "Некорректный JSON");
        return;
      }
    } else {
      policy = formToPolicy(form);
    }

    // Подтверждение для admin-роли при потере wildcard
    if (isAdminRole && !showLockoutWarning) {
      const policyCaps = (policy.capabilities as string[]) ?? [];
      if (!policyCaps.includes("*")) {
        setShowLockoutWarning(true);
        return;
      }
    }
    setShowLockoutWarning(false);

    try {
      await updateMutation.mutateAsync({ roleId: role.id, body: { policy } });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold">{role.name}</h3>
            <span
              className={
                "rounded px-1.5 py-0.5 text-xs " +
                (role.is_builtin
                  ? "bg-primary/10 text-primary"
                  : "bg-muted text-muted-foreground")
              }
            >
              {role.is_builtin ? "встроенная" : "кастомная"}
            </span>
          </div>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        {showLockoutWarning && (
          <div className="mb-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-destructive" />
            <div className="text-sm">
              <p className="font-medium text-destructive">
                Внимание: вы убираете wildcard (*) из прав роли admin.
              </p>
              <p className="mt-1 text-muted-foreground">
                Это может лишить вас административного доступа. Продолжить?
              </p>
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex gap-2 border-b border-border pb-2">
            <button
              type="button"
              onClick={() => {
                setUseRaw(false);
                setShowLockoutWarning(false);
              }}
              className={
                "rounded-md px-3 py-1 text-sm " +
                (!useRaw ? "bg-primary text-primary-foreground" : "text-muted-foreground")
              }
            >
              Форма
            </button>
            <button
              type="button"
              onClick={() => {
                setUseRaw(true);
                setRawJson(JSON.stringify(formToPolicy(form), null, 2));
                setShowLockoutWarning(false);
              }}
              className={
                "rounded-md px-3 py-1 text-sm " +
                (useRaw ? "bg-primary text-primary-foreground" : "text-muted-foreground")
              }
            >
              JSON
            </button>
          </div>

          {useRaw ? (
            <div>
              <textarea
                value={rawJson}
                onChange={(e) => {
                  setRawJson(e.target.value);
                  setJsonError(null);
                }}
                onBlur={() => {
                  try {
                    JSON.parse(rawJson);
                    setJsonError(null);
                  } catch (err) {
                    setJsonError(err instanceof Error ? err.message : "Некорректный JSON");
                  }
                }}
                className="h-64 w-full rounded-md border border-border px-3 py-2 font-mono text-xs"
              />
              {jsonError && (
                <p className="mt-1 text-xs text-destructive">{jsonError}</p>
              )}
            </div>
          ) : (
            <PolicyFields form={form} setForm={setForm} />
          )}

          {willLoseWildcard && !showLockoutWarning && (
            <p className="text-xs text-destructive">
              Внимание: wildcard (*) будет убран из capabilities роли admin.
            </p>
          )}

          <button
            type="submit"
            disabled={updateMutation.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {showLockoutWarning ? "Продолжить" : "Сохранить"}
          </button>
        </form>
      </div>
    </div>
  );
}

function PolicyFields({
  form,
  setForm,
}: {
  form: PolicyForm;
  setForm: (f: PolicyForm) => void;
}) {
  const toggleCapability = (cap: string) => {
    if (form.capabilities.includes(cap)) {
      setForm({ ...form, capabilities: form.capabilities.filter((c) => c !== cap) });
    } else {
      setForm({ ...form, capabilities: [...form.capabilities, cap] });
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <FieldInput
          label="Models (через запятую)"
          value={form.models}
          onChange={(v) => setForm({ ...form, models: v })}
          placeholder="local/*"
        />
        <FieldInput
          label="Corpora (через запятую)"
          value={form.corpora}
          onChange={(v) => setForm({ ...form, corpora: v })}
          placeholder="public, team"
        />
        <FieldInput
          label="Max input tokens"
          value={form.max_input_tokens}
          onChange={(v) => setForm({ ...form, max_input_tokens: v })}
          type="number"
          placeholder="без лимита"
        />
        <FieldInput
          label="Max output tokens"
          value={form.max_output_tokens}
          onChange={(v) => setForm({ ...form, max_output_tokens: v })}
          type="number"
          placeholder="без лимита"
        />
        <FieldInput
          label="RPM"
          value={form.rpm}
          onChange={(v) => setForm({ ...form, rpm: v })}
          type="number"
          placeholder="без лимита"
        />
        <FieldInput
          label="TPM"
          value={form.tpm}
          onChange={(v) => setForm({ ...form, tpm: v })}
          type="number"
          placeholder="без лимита"
        />
        <FieldInput
          label="Budget tokens/month"
          value={form.budget_tokens_month}
          onChange={(v) => setForm({ ...form, budget_tokens_month: v })}
          type="number"
          placeholder="без лимита"
        />
        <FieldInput
          label="Budget cost/month"
          value={form.budget_cost_month}
          onChange={(v) => setForm({ ...form, budget_cost_month: v })}
          type="number"
          placeholder="0"
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Reasoning</label>
        <select
          value={form.reasoning}
          onChange={(e) => setForm({ ...form, reasoning: e.target.value })}
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
        >
          {REASONING_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Capabilities</label>
        <div className="grid grid-cols-2 gap-1">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={form.capabilities.includes("*")}
              onChange={() => toggleCapability("*")}
            />
            * (admin wildcard)
          </label>
          {KNOWN_CAPABILITIES.map((cap) => (
            <label key={cap} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.capabilities.includes(cap)}
                onChange={() => toggleCapability(cap)}
              />
              {cap}
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function FieldInput({
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
