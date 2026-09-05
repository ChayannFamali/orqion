import { useState } from "react";
import { Loader2, Plus, Sparkles, X } from "lucide-react";
import { useCreateSkill, useDeleteSkill, useSkills, useUpdateSkill } from "../hooks/useSkills";
import type { SkillCreate, SkillResponse, SkillUpdate } from "../api/types";

/**
 * Админский каталог скиллов (Т-508).
 *
 * Скилл декларативный: дополнительные инструкции агенту + разрешённый
 * набор инструментов + дефолт числа токенов. Исполняемого содержимого
 * нет — подключение внешних инструментов делается в разделе «Серверы
 * инструментов».
 *
 * Пустой набор инструментов означает, что скилл не даёт агенту доступа
 * ни к одному инструменту: расширение доступа — всегда явное
 * перечисление имён, а не молчаливое следствие регистрации нового
 * сервера. Подпись об этом показывается и в форме, и в карточке.
 *
 * Видимость раздела — по способности ``manage_skills`` в реестре
 * навигации; право проверяется и на сервере (без права — 404). Список
 * для выбора в диалоге отдаётся отдельно всем аутентифицированным.
 */

const EMPTY_TOOLS_HINT =
  "Инструменты не выбраны: скилл не даёт агенту доступа ни к одному инструменту";

/** Имена инструментов из текста поля: по одному на строку. */
function parseTools(toolsText: string): string[] {
  return toolsText
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function SkillsPage() {
  const { data, isLoading, error } = useSkills();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [deletingSkill, setDeletingSkill] = useState<SkillResponse | null>(null);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center" data-testid="skills-loading">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="flex h-full items-center justify-center text-destructive"
        data-testid="skills-error"
      >
        Ошибка загрузки скиллов
      </div>
    );
  }

  const skills = data?.skills ?? [];

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Скиллы</h2>
        <button
          onClick={() => setShowCreateForm(true)}
          data-testid="skills-add"
          className="flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          Добавить
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {skills.length === 0 ? (
          <div
            className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground"
            data-testid="skills-empty"
          >
            <Sparkles className="h-6 w-6" />
            <span>Нет скиллов</span>
          </div>
        ) : (
          <div className="space-y-3">
            {skills.map((skill) => (
              <SkillCard
                key={skill.id}
                skill={skill}
                onEdit={() => setEditingSkill(skill.id)}
                onDelete={() => setDeletingSkill(skill)}
              />
            ))}
          </div>
        )}
      </div>

      {showCreateForm && <CreateSkillModal onClose={() => setShowCreateForm(false)} />}
      {editingSkill && (
        <EditSkillModal
          skill={skills.find((s) => s.id === editingSkill)!}
          onClose={() => setEditingSkill(null)}
        />
      )}
      {deletingSkill && (
        <DeleteSkillModal skill={deletingSkill} onClose={() => setDeletingSkill(null)} />
      )}
    </div>
  );
}

function SkillCard({
  skill,
  onEdit,
  onDelete,
}: {
  skill: SkillResponse;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const updateMutation = useUpdateSkill();

  const handleToggle = () => {
    // Тело собирается явно, а не раскрытием строки каталога: схема правки
    // отвергает неизвестные поля (extra="forbid"), поэтому ``id`` и
    // ``created_at`` в запросе дали бы 422.
    updateMutation.mutate({
      skillId: skill.id,
      body: {
        name: skill.name,
        description: skill.description,
        prompt_text: skill.prompt_text,
        tools: skill.tools,
        default_max_tokens: skill.default_max_tokens,
        enabled: !skill.enabled,
      },
    });
  };

  return (
    <div
      className="rounded-lg border border-border bg-background p-4"
      data-testid={`skill-${skill.name}`}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium">{skill.name}</span>
            <span
              className={`rounded px-1.5 py-0.5 text-xs ${
                skill.enabled
                  ? "bg-green-500/10 text-green-600"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {skill.enabled ? "включён" : "отключён"}
            </span>
          </div>
          {skill.description && (
            <div className="mt-1 truncate text-sm text-muted-foreground">
              {skill.description}
            </div>
          )}
          <div
            className="mt-1 text-xs text-muted-foreground"
            data-testid={`skill-tools-${skill.name}`}
          >
            {skill.tools.length > 0 ? (
              <>Инструменты: {skill.tools.join(", ")}</>
            ) : (
              <span className="text-amber-600 dark:text-amber-400">{EMPTY_TOOLS_HINT}</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            onClick={handleToggle}
            disabled={updateMutation.isPending}
            data-testid={`skill-toggle-${skill.name}`}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            {skill.enabled ? "Отключить" : "Включить"}
          </button>
          <button
            onClick={onEdit}
            data-testid={`skill-edit-${skill.name}`}
            className="rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-accent"
          >
            Изменить
          </button>
          <button
            onClick={onDelete}
            data-testid={`skill-delete-${skill.name}`}
            className="rounded-md border border-destructive/40 px-3 py-1.5 text-sm text-destructive transition-colors hover:bg-destructive/10"
          >
            Удалить
          </button>
        </div>
      </div>
    </div>
  );
}

/** Общие поля формы создания и правки скилла. */
function SkillFormFields({
  name,
  setName,
  description,
  setDescription,
  promptText,
  setPromptText,
  toolsText,
  setToolsText,
  maxTokens,
  setMaxTokens,
}: {
  name: string;
  setName: (value: string) => void;
  description: string;
  setDescription: (value: string) => void;
  promptText: string;
  setPromptText: (value: string) => void;
  toolsText: string;
  setToolsText: (value: string) => void;
  maxTokens: string;
  setMaxTokens: (value: string) => void;
}) {
  const tools = parseTools(toolsText);
  return (
    <>
      <div>
        <label className="mb-1 block text-sm font-medium">Имя</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="skill-form-name"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="Разбор документов"
          required
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Отображается в списке выбора скилла в агентном диалоге.
        </p>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Описание (необязательно)</label>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          data-testid="skill-form-description"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="Для какого сценария"
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">
          Инструкции агенту (необязательно)
        </label>
        <textarea
          value={promptText}
          onChange={(e) => setPromptText(e.target.value)}
          rows={4}
          data-testid="skill-form-prompt"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="Что агент должен делать в этом сценарии"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Добавляются к базовым правилам агента, не заменяют их.
        </p>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">Инструменты</label>
        <textarea
          value={toolsText}
          onChange={(e) => setToolsText(e.target.value)}
          rows={3}
          data-testid="skill-form-tools"
          className="w-full rounded-md border border-border px-3 py-2 text-sm font-mono"
          placeholder={"search_corpus\ndemo-build.get_build_status"}
        />
        <p className="mt-1 text-xs text-muted-foreground">
          По одному имени на строку. Имя внешнего инструмента — в формате
          «сервер.инструмент».
        </p>
        {tools.length === 0 ? (
          <p
            className="mt-1 text-xs text-amber-600 dark:text-amber-400"
            data-testid="skill-form-tools-warning"
          >
            {EMPTY_TOOLS_HINT}
          </p>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">
            Выбрано инструментов: {tools.length}
          </p>
        )}
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium">
          Максимум токенов ответа (необязательно)
        </label>
        <input
          type="number"
          min={1}
          value={maxTokens}
          onChange={(e) => setMaxTokens(e.target.value)}
          data-testid="skill-form-max-tokens"
          className="w-full rounded-md border border-border px-3 py-2 text-sm"
          placeholder="По умолчанию"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Применяется, только если в диалоге не задано своё значение. Значение
          участвует в проверке ограничений роли; фактическую длину ответа
          задают настройки модели.
        </p>
      </div>
    </>
  );
}

function useSkillForm(initial?: SkillResponse) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [promptText, setPromptText] = useState(initial?.prompt_text ?? "");
  const [toolsText, setToolsText] = useState((initial?.tools ?? []).join("\n"));
  const [maxTokens, setMaxTokens] = useState(
    initial?.default_max_tokens == null ? "" : String(initial.default_max_tokens),
  );

  const buildBody = (): SkillCreate => ({
    name: name.trim(),
    description: description.trim(),
    prompt_text: promptText,
    tools: parseTools(toolsText),
    default_max_tokens: maxTokens.trim() === "" ? null : Number(maxTokens),
    enabled: initial?.enabled ?? true,
  });

  return {
    name,
    setName,
    description,
    setDescription,
    promptText,
    setPromptText,
    toolsText,
    setToolsText,
    maxTokens,
    setMaxTokens,
    buildBody,
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

function CreateSkillModal({ onClose }: { onClose: () => void }) {
  const createMutation = useCreateSkill();
  const form = useSkillForm();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync(form.buildBody());
    onClose();
  };

  return (
    <ModalShell title="Новый скилл" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <SkillFormFields {...form} />
        <button
          type="submit"
          disabled={createMutation.isPending}
          data-testid="skill-create-submit"
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          {createMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Создать
        </button>
      </form>
    </ModalShell>
  );
}

function EditSkillModal({
  skill,
  onClose,
}: {
  skill: SkillResponse;
  onClose: () => void;
}) {
  const updateMutation = useUpdateSkill();
  const form = useSkillForm(skill);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: SkillUpdate = form.buildBody();
    await updateMutation.mutateAsync({ skillId: skill.id, body });
    onClose();
  };

  return (
    <ModalShell title="Изменить скилл" onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <SkillFormFields {...form} />
        <p className="text-xs text-muted-foreground">
          Включение и выключение скилла — кнопкой «Отключить»/«Включить» в его
          карточке; текущее состояние здесь сохраняется без изменений.
        </p>
        <button
          type="submit"
          disabled={updateMutation.isPending}
          data-testid="skill-edit-submit"
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
        >
          {updateMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Сохранить
        </button>
      </form>
    </ModalShell>
  );
}

function DeleteSkillModal({
  skill,
  onClose,
}: {
  skill: SkillResponse;
  onClose: () => void;
}) {
  const deleteMutation = useDeleteSkill();

  const handleConfirm = async () => {
    try {
      await deleteMutation.mutateAsync(skill.id);
      onClose();
    } catch {
      // Ошибки удаления — через глобальный обработчик мутаций
    }
  };

  return (
    <ModalShell title="Удалить скилл" onClose={onClose}>
      <div className="space-y-3">
        <p className="text-sm">
          Удалить скилл <span className="font-medium">{skill.name}</span>?
        </p>
        <p className="text-xs text-muted-foreground">
          Диалоги, в которых скилл использовался, сохранятся. Чтобы временно убрать
          скилл из выбора, используйте «Отключить».
        </p>
        <div className="flex gap-2">
          <button
            onClick={handleConfirm}
            disabled={deleteMutation.isPending}
            data-testid="skill-delete-confirm"
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
