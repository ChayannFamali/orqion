import { useState } from "react";
import { Bot, Loader2, Plus, X, MessageSquare, ListTree } from "lucide-react";
import {
  useAgentProfiles,
  useAgentProfileConversations,
  useAvailableAgentProfiles,
  useCreateAgentProfile,
  useDeleteAgentProfile,
  useUpdateAgentProfile,
} from "../hooks/useAgentProfiles";
import { useEnabledModels } from "../hooks/useModels";
import { useAvailableSkills } from "../hooks/useSkills";
import { useCurrentUser } from "../hooks/useAuth";
import type {
  AgentProfileConversationEntry,
  AgentProfileCreate,
  AgentProfileResponse,
  AgentProfileUpdate,
} from "../api/types";

/**
 * Раздел «Агенты» (Т-509): каталог профилей и точка входа для старта диалога.
 *
 * Профиль — переиспользуемая конфигурация агентного диалога: модель + скилл
 * (решение 1). Отдельного промпта и списка инструментов в профиле нет — эту
 * роль выполняет скилл (Т-508).
 *
 * Раздел виден всем аутентифицированным (решение 3): старт диалога от
 * профиля — не админское действие. Поэтому страница показывает всем список
 * доступных профилей с кнопкой «Начать диалог» и опцию «Создать без профиля»
 * (ad-hoc, решение 9). Управление (создание/правка/удаление/выключение
 * профиля и drill-down по диалогам) видно только со способностью
 * ``manage_agents``; право проверяется и на сервере (без него — 404).
 *
 * Drill-down (решение 5) показывает метаданные и расход диалогов профиля,
 * никогда — содержимое переписки. Охват (только свои / все в рабочей
 * области) сервер выбирает сам по праву; поле ``scope`` в ответе делает
 * фактический охват видимым.
 */

interface AgentProfilesPageProps {
  /**
   * Старт агентного диалога: ``profileId`` — диалог от профиля (модель и
   * скилл фиксированы профилем), ``null`` — ad-hoc диалог с ручным выбором
   * модели (решение 9). Обработчик переключает приложение на раздел чата.
   */
  onStartDialog: (profileId: string | null) => void;
}

function hasManageAgents(capabilities: string[]): boolean {
  return capabilities.includes("*") || capabilities.includes("manage_agents");
}

export function AgentProfilesPage({ onStartDialog }: AgentProfilesPageProps) {
  const currentUser = useCurrentUser();
  const capabilities = currentUser.data?.capabilities ?? [];
  const canManage = hasManageAgents(capabilities);

  // Админский каталог загружается только с правом: без него эндпоинт даёт
  // 404, и запрос не должен уходить (паттерн условных запросов).
  const catalog = useAgentProfiles(canManage);
  const available = useAvailableAgentProfiles(true);

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingProfile, setEditingProfile] = useState<string | null>(null);
  const [deletingProfile, setDeletingProfile] = useState<AgentProfileResponse | null>(null);
  // Для drill-down нужны только идентификатор и имя: модалка открывается и
  // из списка выбора (без права управления), где полных полей профиля нет.
  const [drillDownProfile, setDrillDownProfile] = useState<{
    id: string;
    name: string;
  } | null>(null);

  if (canManage) {
    if (catalog.isLoading) {
      return (
        <div className="flex h-full items-center justify-center" data-testid="agent-profiles-loading">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      );
    }
    if (catalog.error) {
      return (
        <div
          className="flex h-full items-center justify-center text-destructive"
          data-testid="agent-profiles-error"
        >
          Ошибка загрузки профилей агентов
        </div>
      );
    }
  }

  const profiles = canManage ? (catalog.data?.profiles ?? []) : [];
  const availableProfiles = available.data?.profiles ?? [];
  const editing = profiles.find((p) => p.id === editingProfile) ?? null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Агенты</h2>
        <div className="flex items-center gap-2">
          {/* Решение 9: ad-hoc путь сохраняется — профили его не отбирают. */}
          <button
            onClick={() => onStartDialog(null)}
            data-testid="agent-start-adhoc"
            className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
            title="Агентный диалог с ручным выбором модели (без профиля)"
          >
            <Bot className="h-4 w-4" />
            Создать без профиля
          </button>
          {canManage && (
            <button
              onClick={() => setShowCreateForm(true)}
              data-testid="agent-profiles-add"
              className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
            >
              <Plus className="h-4 w-4" />
              Добавить профиль
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {canManage ? (
          profiles.length === 0 ? (
            <EmptyProfiles canManage />
          ) : (
            <div className="space-y-3">
              {profiles.map((profile) => (
                <ProfileCard
                  key={profile.id}
                  profile={profile}
                  onStart={() => onStartDialog(profile.id)}
                  onEdit={() => setEditingProfile(profile.id)}
                  onDelete={() => setDeletingProfile(profile)}
                  onDrillDown={() => setDrillDownProfile(profile)}
                />
              ))}
            </div>
          )
        ) : available.isLoading ? (
          <div className="flex h-full items-center justify-center" data-testid="agent-profiles-loading">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : availableProfiles.length === 0 ? (
          <EmptyProfiles canManage={false} />
        ) : (
          <div className="space-y-3">
            {availableProfiles.map((profile) => (
              <AvailableProfileCard
                key={profile.id}
                name={profile.name}
                description={profile.description}
                onStart={() => onStartDialog(profile.id)}
                onDrillDown={() =>
                  setDrillDownProfile({ id: profile.id, name: profile.name })
                }
              />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && <CreateProfileModal onClose={() => setShowCreateForm(false)} />}
      {editing && <EditProfileModal profile={editing} onClose={() => setEditingProfile(null)} />}
      {deletingProfile && (
        <DeleteProfileModal profile={deletingProfile} onClose={() => setDeletingProfile(null)} />
      )}
      {drillDownProfile && (
        <DrillDownModal profile={drillDownProfile} onClose={() => setDrillDownProfile(null)} />
      )}
    </div>
  );
}

function EmptyProfiles({ canManage }: { canManage: boolean }) {
  return (
    <div
      className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground"
      data-testid="agent-profiles-empty"
    >
      <Bot className="h-6 w-6" />
      <span>Нет профилей агентов</span>
      <span className="text-xs">
        {canManage
          ? "Создайте профиль, чтобы закрепить модель и скилл для диалогов, или начните диалог без профиля."
          : "Начните агентный диалог без профиля кнопкой выше."}
      </span>
    </div>
  );
}

/** Карточка профиля в списке выбора (без управления). */
function AvailableProfileCard({
  name,
  description,
  onStart,
  onDrillDown,
}: {
  name: string;
  description: string;
  onStart: () => void;
  onDrillDown: () => void;
}) {
  return (
    <div
      className="flex items-center justify-between gap-4 rounded-lg border border-border bg-background p-4"
      data-testid={`agent-profile-available-${name}`}
    >
      <div className="min-w-0">
        <div className="font-medium">{name}</div>
        {description && (
          <div className="mt-1 truncate text-sm text-muted-foreground">{description}</div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {/* Решение 5: drill-down без права управления доступен и показывает
            только собственные диалоги — точка входа обязана быть, иначе
            разрешённая сервером возможность недостижима из интерфейса. */}
        <button
          onClick={onDrillDown}
          data-testid={`agent-profile-drilldown-${name}`}
          className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          title="Мои диалоги этого профиля: метаданные и расход"
        >
          <ListTree className="h-4 w-4" />
          Мои диалоги
        </button>
        <button
          onClick={onStart}
          data-testid={`agent-profile-start-${name}`}
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <MessageSquare className="h-4 w-4" />
          Начать диалог
        </button>
      </div>
    </div>
  );
}

/** Карточка профиля в админском каталоге: старт + управление + drill-down. */
function ProfileCard({
  profile,
  onStart,
  onEdit,
  onDelete,
  onDrillDown,
}: {
  profile: AgentProfileResponse;
  onStart: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onDrillDown: () => void;
}) {
  const updateMutation = useUpdateAgentProfile();

  const handleToggle = () => {
    // Тело собирается явно: схема правки отвергает неизвестные поля
    // (extra="forbid"), поэтому id/created_at/model_alias в запросе дали бы 422.
    updateMutation.mutate({
      profileId: profile.id,
      body: {
        name: profile.name,
        description: profile.description,
        model_id: profile.model_id,
        skill_id: profile.skill_id,
        enabled: !profile.enabled,
      },
    });
  };

  return (
    <div
      className="rounded-lg border border-border bg-background p-4"
      data-testid={`agent-profile-${profile.name}`}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{profile.name}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs ${
                profile.enabled
                  ? "bg-green-500/10 text-green-600"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {profile.enabled ? "включён" : "отключён"}
            </span>
          </div>
          {profile.description && (
            <div className="mt-1 truncate text-sm text-muted-foreground">
              {profile.description}
            </div>
          )}
          <div className="mt-1 text-xs text-muted-foreground" data-testid={`agent-profile-model-${profile.name}`}>
            Модель: {profile.model_alias}
            {profile.skill_name ? ` · Скилл: ${profile.skill_name}` : " · Без скилла"}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {profile.enabled && (
            <button
              onClick={onStart}
              data-testid={`agent-profile-start-${profile.name}`}
              className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
            >
              <MessageSquare className="h-4 w-4" />
              Начать
            </button>
          )}
          <button
            onClick={onDrillDown}
            data-testid={`agent-profile-drilldown-${profile.name}`}
            className="flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
            title="Диалоги профиля: метаданные и расход"
          >
            <ListTree className="h-4 w-4" />
            Диалоги
          </button>
          <button
            onClick={handleToggle}
            disabled={updateMutation.isPending}
            data-testid={`agent-profile-toggle-${profile.name}`}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            {profile.enabled ? "Отключить" : "Включить"}
          </button>
          <button
            onClick={onEdit}
            data-testid={`agent-profile-edit-${profile.name}`}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            Изменить
          </button>
          <button
            onClick={onDelete}
            data-testid={`agent-profile-delete-${profile.name}`}
            className="rounded-md border border-destructive/40 px-3 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/10"
          >
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Поля формы профиля: имя, описание, модель, скилл.
 *
 * Модель — только из списка с флагом пригодности к инструментам
 * (``supports_tools``): профиль с непригодной моделью сервер отвергает на
 * создании (решение 1, 400). Скилл — из списка доступных (только
 * включённые): привязка отключённого скилла сделала бы профиль
 * неработоспособным в прогоне. Оба списка доступны любому
 * аутентифицированному, поэтому 404 при загрузке формы исключён.
 */
function ProfileFormFields({
  name,
  setName,
  description,
  setDescription,
  modelId,
  setModelId,
  skillId,
  setSkillId,
  agentModels,
  skills,
}: {
  name: string;
  setName: (value: string) => void;
  description: string;
  setDescription: (value: string) => void;
  modelId: string;
  setModelId: (value: string) => void;
  skillId: string;
  setSkillId: (value: string) => void;
  agentModels: { id: string; alias: string }[];
  skills: { id: string; name: string }[];
}) {
  return (
    <>
      <div>
        <label className="mb-1 block text-sm font-medium">Имя</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="agent-profile-form-name"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="Аналитик"
          required
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Уникально в рабочей области. Отображается в списке выбора при старте диалога.
        </p>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Описание (необязательно)</label>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          data-testid="agent-profile-form-description"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="Для какого сценария"
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Модель</label>
        <select
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          data-testid="agent-profile-form-model"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          required
        >
          <option value="" disabled>
            Выберите модель
          </option>
          {agentModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.alias}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-muted-foreground">
          Только модели, отмеченные администратором как пригодные для агентного режима.
          {agentModels.length === 0 && " таких моделей пока нет — профиль создать нельзя."}
        </p>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Скилл (необязательно)</label>
        <select
          value={skillId}
          onChange={(e) => setSkillId(e.target.value)}
          data-testid="agent-profile-form-skill"
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        >
          <option value="">Без скилла</option>
          {skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-muted-foreground">
          Скилл задаёт инструкции агенту и разрешённый набор инструментов. Без скилла
          профиль — агент без сужения (как ad-hoc диалог).
        </p>
      </div>
    </>
  );
}

function useProfileForm(initial?: AgentProfileResponse) {
  const models = useEnabledModels();
  const skills = useAvailableSkills(true);
  const agentModels = (models.data ?? [])
    .filter((m) => m.supports_tools)
    .map((m) => ({ id: m.id, alias: m.alias }));
  const skillOptions = (skills.data?.skills ?? []).map((s) => ({ id: s.id, name: s.name }));

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [modelId, setModelId] = useState(initial?.model_id ?? "");
  const [skillId, setSkillId] = useState(initial?.skill_id ?? "");

  const buildBody = (): AgentProfileCreate => ({
    name: name.trim(),
    description: description.trim(),
    model_id: modelId,
    skill_id: skillId === "" ? null : skillId,
    enabled: initial?.enabled ?? true,
  });

  return {
    name,
    setName,
    description,
    setDescription,
    modelId,
    setModelId,
    skillId,
    setSkillId,
    agentModels,
    skills: skillOptions,
    buildBody,
    // Создание невозможно, пока не выбрана пригодная модель.
    canSubmit: modelId !== "",
  };
}

function ModalShell({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function CreateProfileModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateAgentProfile();
  const form = useProfileForm();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.canSubmit) return;
    await createMutation.mutateAsync(form.buildBody());
    onClose();
  };

  return (
    <ModalShell title="Новый профиль агента" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <ProfileFormFields {...form} />
        <button
          type="submit"
          disabled={createMutation.isPending || !form.canSubmit}
          data-testid="agent-profile-create-submit"
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {createMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Создать
        </button>
      </form>
    </ModalShell>
  );
}

function EditProfileModal({
  profile,
  onClose,
}: {
  profile: AgentProfileResponse;
  onClose: () => void;
}) {
  const updateMutation = useUpdateAgentProfile();
  const form = useProfileForm(profile);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.canSubmit) return;
    const body: AgentProfileUpdate = form.buildBody();
    await updateMutation.mutateAsync({ profileId: profile.id, body });
    onClose();
  };

  return (
    <ModalShell title="Изменить профиль" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <ProfileFormFields {...form} />
        <p className="text-xs text-muted-foreground">
          Смена модели или скилла влияет на все диалоги профиля: конфигурация
          фиксируется профилем, а не копией в диалоге (решение 2).
        </p>
        <button
          type="submit"
          disabled={updateMutation.isPending || !form.canSubmit}
          data-testid="agent-profile-edit-submit"
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Сохранить
        </button>
      </form>
    </ModalShell>
  );
}

function DeleteProfileModal({
  profile,
  onClose,
}: {
  profile: AgentProfileResponse;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteAgentProfile();

  const handleConfirm = async () => {
    try {
      await deleteMutation.mutateAsync(profile.id);
      onClose();
    } catch {
      // Ошибки удаления (в т.ч. 409 при наличии диалогов) — через глобальный
      // обработчик мутаций.
    }
  };

  return (
    <ModalShell title="Удалить профиль" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm">
          Удалить профиль <span className="font-medium">{profile.name}</span>?
        </p>
        <p className="text-xs text-muted-foreground">
          Удаление заблокировано, если у профиля есть диалоги (решение 8) — в этом
          случае используйте «Отключить»: профиль исчезнет из выбора, а диалоги
          сохранятся.
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleConfirm}
            disabled={deleteMutation.isPending}
            data-testid="agent-profile-delete-confirm"
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
    </ModalShell>
  );
}

/**
 * Drill-down по диалогам профиля (решение 5): метаданные и расход.
 * Содержимое переписки не отдаётся никогда — в ответе сервера полей
 * сообщения нет вовсе. Заголовок заполнен только для собственных диалогов.
 */
function DrillDownModal({
  profile,
  onClose,
}: {
  profile: { id: string; name: string };
  onClose: () => void;
}) {
  const { data, isLoading, error } = useAgentProfileConversations(profile.id);
  const conversations: AgentProfileConversationEntry[] = data?.conversations ?? [];
  const scope = data?.scope;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-background p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold">Диалоги профиля «{profile.name}»</h3>
          <button onClick={onClose}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>

        <p className="mb-3 text-xs text-muted-foreground" data-testid="agent-profile-drilldown-scope">
          {scope === "workspace"
            ? "Все диалоги рабочей области: метаданные и расход. Содержимое переписки недоступно."
            : "Ваши диалоги этого профиля. Содержимое переписки недоступно."}
        </p>

        {isLoading ? (
          <div className="flex items-center justify-center py-8" data-testid="agent-profile-drilldown-loading">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="py-8 text-center text-destructive" data-testid="agent-profile-drilldown-error">
            Ошибка загрузки диалогов
          </div>
        ) : conversations.length === 0 ? (
          <div className="py-8 text-center text-muted-foreground" data-testid="agent-profile-drilldown-empty">
            У профиля пока нет диалогов
          </div>
        ) : (
          <table className="w-full text-sm" data-testid="agent-profile-drilldown-table">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="py-2 pr-2 font-medium">Диалог</th>
                <th className="py-2 pr-2 font-medium">Запросов</th>
                <th className="py-2 pr-2 font-medium">Токенов</th>
                <th className="py-2 font-medium">Активность</th>
              </tr>
            </thead>
            <tbody>
              {conversations.map((c) => (
                <tr key={c.id} className="border-b border-border/50">
                  <td className="py-2 pr-2">
                    <span className="font-mono text-xs">{c.id.slice(0, 8)}</span>
                    {c.title ? (
                      <span className="ml-2 text-muted-foreground">{c.title}</span>
                    ) : (
                      <span className="ml-2 text-muted-foreground/60">(заголовок скрыт)</span>
                    )}
                    {c.archived && (
                      <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                        архив
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-2">{c.requests}</td>
                  <td className="py-2 pr-2">
                    {c.tokens_in + c.tokens_out}
                  </td>
                  <td className="py-2 text-xs text-muted-foreground">
                    {new Date(c.last_activity_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
