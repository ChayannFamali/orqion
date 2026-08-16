import { useState } from "react";
import { Loader2, X, AlertTriangle, UserCog, UserPlus, Copy, Check } from "lucide-react";
import { useUsers } from "../hooks/useUsers";
import { useRoles } from "../hooks/useRoles";
import { useUpdateUser, useImpersonateUser } from "../hooks/useUsers";
import { apiCreateUser } from "../api/users";
import { apiChangePassword } from "../api/auth";
import type { UserListItem, UserCreateResponse } from "../api/types";

export function UsersPage() {
  const { data, isLoading, error } = useUsers();
  const [editingUser, setEditingUser] = useState<UserListItem | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createdUser, setCreatedUser] = useState<UserCreateResponse | null>(null);

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
        Ошибка загрузки пользователей
      </div>
    );
  }

  const users = data?.users ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Пользователи</h2>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <UserPlus className="h-4 w-4" />
          Создать
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {users.length === 0 ? (
          <div className="flex h-full items-center justify-center text-muted-foreground">
            Нет пользователей
          </div>
        ) : (
          <div className="space-y-3">
            {users.map((user) => (
              <UserCard
                key={user.id}
                user={user}
                onEdit={() => setEditingUser(user)}
              />
            ))}
          </div>
        )}
      </div>

      {editingUser && (
        <EditUserModal user={editingUser} onClose={() => setEditingUser(null)} />
      )}

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onCreated={(resp) => {
            setShowCreate(false);
            setCreatedUser(resp);
          }}
        />
      )}

      {createdUser && (
        <PasswordDialog
          user={createdUser}
          onClose={() => setCreatedUser(null)}
        />
      )}
    </div>
  );
}

function UserCard({
  user,
  onEdit,
}: {
  user: UserListItem;
  onEdit: () => void;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{user.email}</span>
            <span
              className={
                "rounded px-1.5 py-0.5 text-xs " +
                (user.is_active
                  ? "bg-primary/10 text-primary"
                  : "bg-destructive/10 text-destructive")
              }
            >
              {user.is_active ? "активен" : "отключён"}
            </span>
            {user.must_change_password && (
              <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-600">
                смена пароля
              </span>
            )}
          </div>
          <div className="text-sm text-muted-foreground">
            Роль: {user.role_name}
            {user.is_builtin_role && " (встроенная)"}
            {user.team_name && ` · Команда: ${user.team_name}`}
          </div>
        </div>
        <button
          onClick={onEdit}
          className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs transition-colors hover:bg-accent"
        >
          <UserCog className="h-3 w-3" />
          Изменить
        </button>
      </div>
    </div>
  );
}

function CreateUserModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (resp: UserCreateResponse) => void;
}) {
  const { data: rolesData } = useRoles();
  const [email, setEmail] = useState("");
  const [roleId, setRoleId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const roles = rolesData?.roles ?? [];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !roleId) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await apiCreateUser({ email, role_id: roleId });
      onCreated(resp);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка создания пользователя");
    } finally {
      setLoading(false);
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
          <h3 className="text-lg font-semibold">Создать пользователя</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              placeholder="user@orqion.local"
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Роль</label>
            <select
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
            >
              <option value="">Выберите роль…</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                  {role.is_builtin ? " (встроенная)" : ""}
                </option>
              ))}
            </select>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !email || !roleId}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            Создать
          </button>
        </form>
      </div>
    </div>
  );
}

function PasswordDialog({
  user,
  onClose,
}: {
  user: UserCreateResponse;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(user.password);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard может не работать вне HTTPS
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Пользователь создан</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <div className="space-y-4">
          <div className="text-sm text-muted-foreground">
            Пользователь <span className="font-medium">{user.email}</span> создан.
            Пароль показывается <span className="font-medium text-amber-600">один раз</span> —
            передайте его пользователю. При первом входе потребуется смена пароля.
          </div>

          <div className="rounded-md border border-border bg-muted/50 p-4">
            <div className="mb-2 text-xs text-muted-foreground">Сгенерированный пароль:</div>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded bg-background px-3 py-2 font-mono text-sm">
                {user.password}
              </code>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 rounded-md border border-border px-2 py-2 text-sm transition-colors hover:bg-accent"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-600" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
                {copied ? "Скопировано" : "Копировать"}
              </button>
            </div>
          </div>

          <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
            <div className="text-sm text-amber-700">
              Этот пароль больше не будет показан. Сохраните его в безопасном месте.
            </div>
          </div>

          <button
            onClick={onClose}
            className="w-full rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Готово
          </button>
        </div>
      </div>
    </div>
  );
}

function EditUserModal({
  user,
  onClose,
}: {
  user: UserListItem;
  onClose: () => void;
}) {
  const { data: rolesData } = useRoles();
  const updateMutation = useUpdateUser();
  const impersonateMutation = useImpersonateUser();
  const [roleId, setRoleId] = useState(user.role_id);
  const [isActive, setIsActive] = useState(user.is_active);
  const [showImpersonateConfirm, setShowImpersonateConfirm] = useState(false);

  const roles = rolesData?.roles ?? [];

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: { role_id?: string; is_active?: boolean } = {};
    if (roleId !== user.role_id) body.role_id = roleId;
    if (isActive !== user.is_active) body.is_active = isActive;

    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }

    try {
      await updateMutation.mutateAsync({ userId: user.id, body });
      onClose();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
    }
  };

  const handleImpersonate = async () => {
    if (!showImpersonateConfirm) {
      setShowImpersonateConfirm(true);
      return;
    }
    setShowImpersonateConfirm(false);
    try {
      await impersonateMutation.mutateAsync(user.id);
      window.location.reload();
    } catch {
      // Ошибка показывается через глобальный mutations.onError → toast
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
          <h3 className="text-lg font-semibold">{user.email}</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Роль</label>
            <select
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
            >
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                  {role.is_builtin ? " (встроенная)" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="mb-1 block text-sm font-medium">Статус</label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={isActive}
                onChange={(e) => setIsActive(e.target.checked)}
              />
              Активен
            </label>
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

        <div className="mt-4 border-t border-border pt-4">
          {showImpersonateConfirm && (
            <div className="mb-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-destructive" />
              <div className="text-sm">
                <p className="font-medium text-destructive">
                  Войти от имени {user.email}?
                </p>
                <p className="mt-1 text-muted-foreground">
                  Вы будете переключены на этого пользователя. Используйте кнопку выхода
                  из имперсонации для возврата.
                </p>
              </div>
            </div>
          )}
          <button
            onClick={handleImpersonate}
            disabled={impersonateMutation.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-border px-4 py-2 text-sm transition-colors hover:bg-accent"
          >
            {impersonateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {showImpersonateConfirm ? "Подтвердить вход" : "Войти от имени"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ChangePasswordModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!oldPassword || !newPassword || !confirmPassword) return;
    if (newPassword !== confirmPassword) {
      setError("Пароли не совпадают");
      return;
    }
    if (newPassword === oldPassword) {
      setError("Новый пароль не должен совпадать с текущим");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await apiChangePassword({ old_password: oldPassword, new_password: newPassword });
      onClose();
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка смены пароля");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-6">
        <div className="mb-4">
          <h3 className="text-lg font-semibold">Смена пароля</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Необходимо сменить пароль перед продолжением работы.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium">Текущий пароль</label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Новый пароль</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Повторите пароль</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full rounded-md border border-border px-3 py-2 text-sm"
              required
            />
          </div>

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            Сменить пароль
          </button>
        </form>
      </div>
    </div>
  );
}
