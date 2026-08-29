import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsPage } from "../pages/SettingsPage";
import { useRagSettings, useUpdateRagSettings } from "../hooks/useRagSettings";
import {
  useCreatePromptTemplate,
  useDeletePromptTemplate,
  usePromptTemplates,
  useUpdatePromptTemplate,
} from "../hooks/usePromptTemplates";
import { toast } from "sonner";

/**
 * T-506: настройки RAG-поиска уровня рабочей области.
 * Т-505 добавила третье поле — число групп графа документов (2–20).
 *
 * Приёмка: чтение для всех; изменение только с правом управления корпусами;
 * формулировки «Порог релевантности после реранкинга» и «Максимум фрагментов
 * контекста»; пресеты — подсказки, не кнопки; валидация диапазонов.
 */

vi.mock("../hooks/useRagSettings", () => ({
  useRagSettings: vi.fn(),
  useUpdateRagSettings: vi.fn(),
}));
vi.mock("../hooks/usePromptTemplates", () => ({
  usePromptTemplates: vi.fn(),
  useCreatePromptTemplate: vi.fn(),
  useUpdatePromptTemplate: vi.fn(),
  useDeletePromptTemplate: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mutateAsync = vi.fn();
const createMutate = vi.fn();
const updateMutate = vi.fn();
const deleteMutate = vi.fn();

function mockHooks(
  data?: { relevance_threshold: number; max_fragments: number; cluster_count?: number },
  opts?: { isLoading?: boolean; isError?: boolean },
) {
  const settings = data ? { cluster_count: 8, ...data } : undefined;
  vi.mocked(useRagSettings).mockReturnValue({
    data: settings,
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
  } as unknown as ReturnType<typeof useRagSettings>);
  vi.mocked(useUpdateRagSettings).mockReturnValue({
    mutate: mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateRagSettings>);
  mockPromptHooks();
}

type PromptTemplate = {
  id: string;
  title: string;
  body: string;
  created_at: string;
};

function mockPromptHooks(templates: PromptTemplate[] = []) {
  vi.mocked(usePromptTemplates).mockReturnValue({
    data: { templates },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof usePromptTemplates>);
  vi.mocked(useCreatePromptTemplate).mockReturnValue({
    mutate: createMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useCreatePromptTemplate>);
  vi.mocked(useUpdatePromptTemplate).mockReturnValue({
    mutate: updateMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useUpdatePromptTemplate>);
  vi.mocked(useDeletePromptTemplate).mockReturnValue({
    mutate: deleteMutate,
    isPending: false,
  } as unknown as ReturnType<typeof useDeletePromptTemplate>);
}

const ADMIN = ["*"];
const MANAGER = ["chat", "upload", "manage_corpora"];
const USER = ["chat", "upload"];

describe("SettingsPage (T-506)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("показывает вкладку «Поиск по документам» и заголовки", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={ADMIN} />);

    expect(screen.getByTestId("settings-tab-search")).toHaveTextContent(
      "Поиск по документам",
    );
    expect(screen.getByText("Порог релевантности после реранкинга")).toBeInTheDocument();
    expect(screen.getByText("Максимум фрагментов контекста")).toBeInTheDocument();
  });

  it("подставляет текущие значения в поля", () => {
    mockHooks({ relevance_threshold: 40, max_fragments: 5, cluster_count: 12 });
    render(<SettingsPage capabilities={ADMIN} />);

    expect(screen.getByTestId("rag-threshold-input")).toHaveValue(40);
    expect(screen.getByTestId("rag-max-fragments-input")).toHaveValue(5);
    expect(screen.getByTestId("rag-cluster-count-input")).toHaveValue(12);
  });

  it("для роли без права управления корпусами — только чтение", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={USER} />);

    expect(screen.getByTestId("rag-threshold-input")).toBeDisabled();
    expect(screen.getByTestId("rag-max-fragments-input")).toBeDisabled();
    expect(screen.getByTestId("rag-cluster-count-input")).toBeDisabled();
    expect(screen.getByTestId("rag-settings-readonly")).toBeInTheDocument();
    expect(screen.queryByTestId("rag-settings-save")).not.toBeInTheDocument();
  });

  it("кнопка сохранения неактивна без изменений", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={MANAGER} />);

    expect(screen.getByTestId("rag-settings-save")).toBeDisabled();
  });

  it("сохраняет изменения с правом управления корпусами", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={MANAGER} />);

    fireEvent.change(screen.getByTestId("rag-threshold-input"), { target: { value: "50" } });
    fireEvent.change(screen.getByTestId("rag-max-fragments-input"), {
      target: { value: "4" },
    });

    const save = screen.getByTestId("rag-settings-save");
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    expect(mutateAsync).toHaveBeenCalledWith(
      { relevance_threshold: 50, max_fragments: 4, cluster_count: 8 },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("отклоняет порог вне диапазона 0–100", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={ADMIN} />);

    fireEvent.change(screen.getByTestId("rag-threshold-input"), { target: { value: "150" } });
    fireEvent.change(screen.getByTestId("rag-max-fragments-input"), {
      target: { value: "4" },
    });
    fireEvent.click(screen.getByTestId("rag-settings-save"));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "Порог релевантности — целое число от 0 до 100",
    );
  });

  it("отклоняет максимум фрагментов вне диапазона 1–8", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={ADMIN} />);

    fireEvent.change(screen.getByTestId("rag-max-fragments-input"), {
      target: { value: "12" },
    });
    fireEvent.click(screen.getByTestId("rag-settings-save"));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Максимум фрагментов — целое число от 1 до 8");
  });

  it("отклоняет число групп вне диапазона 2–20", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={ADMIN} />);

    fireEvent.change(screen.getByTestId("rag-cluster-count-input"), {
      target: { value: "25" },
    });
    fireEvent.click(screen.getByTestId("rag-settings-save"));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "Число групп графа документов — целое число от 2 до 20",
    );
  });

  it("сохраняет изменённое число групп", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8, cluster_count: 8 });
    render(<SettingsPage capabilities={MANAGER} />);

    fireEvent.change(screen.getByTestId("rag-cluster-count-input"), {
      target: { value: "6" },
    });

    const save = screen.getByTestId("rag-settings-save");
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    expect(mutateAsync).toHaveBeenCalledWith(
      { relevance_threshold: 0, max_fragments: 8, cluster_count: 6 },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("показывает сообщение при ошибке загрузки", () => {
    mockHooks(undefined, { isError: true });
    render(<SettingsPage capabilities={ADMIN} />);

    expect(screen.getByText("Не удалось загрузить настройки поиска.")).toBeInTheDocument();
  });
});

const DEVELOPER = ["chat", "upload", "custom_prompts"];
const SUPPORT = ["chat"];

const SAMPLE_TEMPLATE: PromptTemplate = {
  id: "pt1",
  title: "Код-ревью",
  body: "Проведи код-ревью следующего файла",
  created_at: "2026-08-29T10:00:00Z",
};

describe("SettingsPage — вкладка «Шаблоны промптов» (T-507)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("вкладка видна со способностью custom_prompts и скрыта без неё", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    const { unmount } = render(<SettingsPage capabilities={DEVELOPER} />);
    expect(screen.getByTestId("settings-tab-prompts")).toBeInTheDocument();
    unmount();

    render(<SettingsPage capabilities={SUPPORT} />);
    expect(screen.queryByTestId("settings-tab-prompts")).not.toBeInTheDocument();
  });

  it("показывает список шаблонов на вкладке", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks([SAMPLE_TEMPLATE]);
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));

    expect(screen.getAllByTestId("prompt-template-item")).toHaveLength(1);
    expect(screen.getByText("Код-ревью")).toBeInTheDocument();
  });

  it("создаёт шаблон через форму", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks();
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));
    fireEvent.click(screen.getByTestId("prompt-template-create"));

    fireEvent.change(screen.getByTestId("prompt-template-title"), {
      target: { value: "Новый шаблон" },
    });
    fireEvent.change(screen.getByTestId("prompt-template-body"), {
      target: { value: "Текст шаблона" },
    });
    fireEvent.click(screen.getByTestId("prompt-template-save"));

    expect(createMutate).toHaveBeenCalledWith(
      { title: "Новый шаблон", body: "Текст шаблона" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("не создаёт шаблон с пустым названием", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks();
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));
    fireEvent.click(screen.getByTestId("prompt-template-create"));
    fireEvent.change(screen.getByTestId("prompt-template-body"), {
      target: { value: "Текст" },
    });
    fireEvent.click(screen.getByTestId("prompt-template-save"));

    expect(createMutate).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Название шаблона не может быть пустым");
  });

  it("удаляет шаблон после подтверждения", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks([SAMPLE_TEMPLATE]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));
    fireEvent.click(screen.getByTestId("prompt-template-delete"));

    expect(deleteMutate).toHaveBeenCalledWith(
      "pt1",
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("редактирование подставляет значения в форму", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks([SAMPLE_TEMPLATE]);
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));
    fireEvent.click(screen.getByTestId("prompt-template-edit"));

    expect(screen.getByTestId("prompt-template-title")).toHaveValue("Код-ревью");
    expect(screen.getByTestId("prompt-template-body")).toHaveValue(
      "Проведи код-ревью следующего файла",
    );
  });

  it("показывает пустое состояние без шаблонов", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    mockPromptHooks();
    render(<SettingsPage capabilities={DEVELOPER} />);

    fireEvent.click(screen.getByTestId("settings-tab-prompts"));

    expect(screen.getByText("Шаблонов пока нет.")).toBeInTheDocument();
  });
});
