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
import {
  useUpdateWorkspaceSetting,
  useWorkspaceSettings,
} from "../hooks/useWorkspaceSettings";
import { toast } from "sonner";
import type { ApiError } from "../api/runtime";
import type { WorkspaceSettingResponse } from "../api/types";

/**
 * Вкладка «Общие» в разделе настроек: реестр служебных настроек.
 *
 * Ключевое требование — состав вкладки определяется ответом API, а не кодом:
 * ни один ключ не упомянут в компоненте по имени, поэтому новый ключ в
 * реестре появляется в интерфейсе без правки фронтенда. Проверяется на
 * вымышленных ключах всех четырёх типов.
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
vi.mock("../hooks/useWorkspaceSettings", () => ({
  useWorkspaceSettings: vi.fn(),
  useUpdateWorkspaceSetting: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const updateMutate = vi.fn();

function setting(overrides: Partial<WorkspaceSettingResponse>): WorkspaceSettingResponse {
  return {
    key: "k",
    title: "Заголовок",
    description: "Описание",
    category: "Общие",
    type: "string",
    enum_values: null,
    value: "",
    source: "default",
    editable: true,
    min: null,
    max: null,
    ...overrides,
  } as WorkspaceSettingResponse;
}

/** По одному ключу каждого типа — имена вымышленные, компонент их не знает. */
const ALL_TYPES: WorkspaceSettingResponse[] = [
  setting({
    key: "session_days",
    title: "Срок жизни сессии (дней)",
    category: "Сессии",
    type: "integer",
    value: 7,
    min: 1,
    max: 365,
  }),
  setting({
    key: "generation_mode",
    title: "Режим генерации",
    category: "Сессии",
    type: "enum",
    value: "balanced",
    enum_values: ["fast", "balanced", "thorough"],
  }),
  setting({
    key: "compact_list",
    title: "Компактный список",
    category: "Сессии",
    type: "boolean",
    value: false,
  }),
  setting({
    key: "login_note",
    title: "Заметка на странице входа",
    category: "Сессии",
    type: "string",
    value: "",
    source: "db",
  }),
];

function mockSettings(settings: WorkspaceSettingResponse[], opts?: { isLoading?: boolean; isError?: boolean }) {
  vi.mocked(useWorkspaceSettings).mockReturnValue({
    data: { settings },
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
  } as unknown as ReturnType<typeof useWorkspaceSettings>);
  vi.mocked(useUpdateWorkspaceSetting).mockReturnValue({
    mutate: updateMutate,
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useUpdateWorkspaceSetting>);
}

/** Соседние вкладки не рендерятся, но их хуки импортированы — глушим. */
function mockSiblingTabs() {
  vi.mocked(useRagSettings).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useRagSettings>);
  vi.mocked(useUpdateRagSettings).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateRagSettings>);
  vi.mocked(usePromptTemplates).mockReturnValue({
    data: { templates: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof usePromptTemplates>);
  vi.mocked(useCreatePromptTemplate).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCreatePromptTemplate>);
  vi.mocked(useUpdatePromptTemplate).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUpdatePromptTemplate>);
  vi.mocked(useDeletePromptTemplate).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useDeletePromptTemplate>);
}

function openGeneralTab(capabilities: string[] = ["*"]) {
  mockSiblingTabs();
  const view = render(<SettingsPage capabilities={capabilities} />);
  fireEvent.click(screen.getByTestId("settings-tab-general"));
  return view;
}

describe("Настройки — вкладка «Общие»", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("вкладка видна всем аутентифицированным, без гейта по способности", () => {
    mockSettings([]);
    openGeneralTab(["chat"]);

    expect(screen.getByTestId("settings-tab-general")).toHaveTextContent("Общие");
    expect(screen.getByTestId("workspace-settings-empty")).toBeInTheDocument();
  });

  it("при пустом реестре показывает «Настроек пока нет» без формы", () => {
    mockSettings([]);
    openGeneralTab();

    expect(screen.getByTestId("workspace-settings-empty")).toHaveTextContent(
      "Настроек пока нет.",
    );
    expect(screen.queryAllByTestId(/^setting-block-/)).toHaveLength(0);
  });

  it("рисует поле по типу из ответа: число, перечисление, флаг, строка", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    const number = screen.getByTestId("setting-field-session_days");
    expect(number).toHaveAttribute("type", "number");
    expect(number).toHaveAttribute("min", "1");
    expect(number).toHaveAttribute("max", "365");
    expect(number).toHaveValue(7);

    const select = screen.getByTestId("setting-field-generation_mode");
    expect(select.tagName).toBe("SELECT");
    expect(Array.from(select.querySelectorAll("option")).map((o) => o.value)).toEqual([
      "fast",
      "balanced",
      "thorough",
    ]);
    expect(select).toHaveValue("balanced");

    expect(screen.getByTestId("setting-field-compact_list")).toHaveAttribute(
      "type",
      "checkbox",
    );
    expect(screen.getByTestId("setting-field-login_note")).toHaveAttribute("type", "text");
  });

  it("дробный ключ: шаг ввода не ограничивает сеткой, дробное значение уходит на сервер", () => {
    mockSettings([
      setting({
        key: "session_days",
        type: "integer",
        value: 7,
        min: 1,
        max: 365,
      }),
      setting({
        key: "sample_ratio",
        title: "Доля выборки",
        type: "number",
        value: 0.7,
        min: 0,
        max: 2,
      }),
    ]);
    openGeneralTab();

    expect(screen.getByTestId("setting-field-session_days")).toHaveAttribute("step", "1");
    // Без «any» браузер допускает только значения сетки шага и подсвечивает
    // дробное как ошибку, хотя сервер его принимает.
    expect(screen.getByTestId("setting-field-sample_ratio")).toHaveAttribute("step", "any");

    const input = screen.getByTestId("setting-field-sample_ratio");
    fireEvent.change(input, { target: { value: "0.35" } });
    fireEvent.blur(input);

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toEqual({ key: "sample_ratio", value: 0.35 });
  });

  it("показывает заголовок и описание из ответа, а не из кода", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    expect(screen.getByText("Срок жизни сессии (дней)")).toBeInTheDocument();
    expect(screen.getAllByText("Описание").length).toBe(4);
  });

  it("метка источника: «по умолчанию» и «изменено»", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    expect(screen.getByTestId("setting-source-session_days")).toHaveTextContent(
      "по умолчанию",
    );
    expect(screen.getByTestId("setting-source-login_note")).toHaveTextContent("изменено");
  });

  it("без права на ключ поле только для чтения, но не скрыто", () => {
    mockSettings([
      setting({ key: "session_days", type: "integer", value: 7, editable: false }),
      setting({ key: "login_note", type: "string", value: "", editable: true }),
    ]);
    openGeneralTab(["chat"]);

    expect(screen.getByTestId("setting-field-session_days")).toBeDisabled();
    expect(screen.getByTestId("setting-readonly-session_days")).toBeInTheDocument();
    expect(screen.getByTestId("setting-field-login_note")).not.toBeDisabled();
    expect(screen.queryByTestId("setting-readonly-login_note")).not.toBeInTheDocument();
  });

  it("сохраняет один ключ за вызов при потере фокуса", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.blur(input);

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toEqual({ key: "session_days", value: 30 });
  });

  it("не отправляет запрос, если значение не изменилось", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.blur(input);

    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("отказ по нецелому числу показывается инлайн и не уходит на сервер", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "7.5" } });
    fireEvent.blur(input);

    expect(updateMutate).not.toHaveBeenCalled();
    expect(screen.getByTestId("setting-error-session_days")).toHaveTextContent(
      "Введите целое число",
    );
    expect(screen.getByTestId("setting-field-login_note")).toBeInTheDocument();
  });

  it("пустое числовое поле не уходит на сервер", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);

    expect(updateMutate).not.toHaveBeenCalled();
    expect(screen.getByTestId("setting-error-session_days")).toHaveTextContent(
      "Введите значение",
    );
  });

  it("переключение флага и выбор из списка уходят на сервер сразу", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    fireEvent.click(screen.getByTestId("setting-field-compact_list"));
    expect(updateMutate).toHaveBeenLastCalledWith(
      { key: "compact_list", value: true },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    fireEvent.change(screen.getByTestId("setting-field-generation_mode"), {
      target: { value: "thorough" },
    });
    expect(updateMutate).toHaveBeenLastCalledWith(
      { key: "generation_mode", value: "thorough" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it("успешное сохранение обновляет значение и метку источника", () => {
    mockSettings(ALL_TYPES);
    updateMutate.mockImplementation((_body, handlers) => handlers?.onSuccess?.());
    const view = openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "30" } });
    fireEvent.blur(input);

    expect(toast.success).toHaveBeenCalledWith("Настройка сохранена");

    // Ответ сервера после записи: значение из БД, источник «изменено».
    mockSettings([
      ...ALL_TYPES.slice(1),
      { ...ALL_TYPES[0], value: 30, source: "db" as const },
    ]);
    view.rerender(<SettingsPage capabilities={["*"]} />);

    expect(screen.getByTestId("setting-field-session_days")).toHaveValue(30);
    expect(screen.getByTestId("setting-source-session_days")).toHaveTextContent("изменено");
  });

  it("отказ сервера показывается инлайн под полем и не роняет форму", () => {
    mockSettings(ALL_TYPES);
    const apiError: ApiError = {
      error: "setting_value_invalid",
      reason: "Значение настройки не соответствует её описанию",
      constraint: { key: "session_days", expected: "integer", min: 1, max: 365 },
      hint: "Ожидается целое число от 1 до 365",
    };
    updateMutate.mockImplementation((_body, handlers) => handlers?.onError?.(apiError));
    openGeneralTab();

    const input = screen.getByTestId("setting-field-session_days");
    fireEvent.change(input, { target: { value: "999" } });
    fireEvent.blur(input);

    expect(screen.getByTestId("setting-error-session_days")).toHaveTextContent(
      "Ожидается целое число от 1 до 365",
    );
    // Черновик вернулся к значению сервера, остальные поля целы.
    expect(input).toHaveValue(7);
    expect(screen.getByTestId("setting-field-login_note")).toBeInTheDocument();
    expect(screen.getByTestId("setting-field-generation_mode")).toBeInTheDocument();
  });

  it("ошибка без подсказки показывает причину", () => {
    mockSettings(ALL_TYPES);
    updateMutate.mockImplementation((_body, handlers) =>
      handlers?.onError?.({
        error: "not_found",
        reason: "Нет права на изменение этой настройки",
        constraint: null,
        hint: null,
      } satisfies ApiError),
    );
    openGeneralTab();

    fireEvent.change(screen.getByTestId("setting-field-login_note"), {
      target: { value: "Обед" },
    });
    fireEvent.blur(screen.getByTestId("setting-field-login_note"));

    expect(screen.getByTestId("setting-error-login_note")).toHaveTextContent(
      "Нет права на изменение этой настройки",
    );
  });

  it("одна категория — список полей без подвкладок", () => {
    mockSettings(ALL_TYPES);
    openGeneralTab();

    expect(screen.queryByTestId("workspace-settings-categories")).not.toBeInTheDocument();
    expect(screen.getByText("Сессии")).toBeInTheDocument();
    expect(screen.getAllByTestId(/^setting-block-/)).toHaveLength(4);
  });

  it("несколько категорий — подвкладки строятся из ответа, не хардкодом", () => {
    mockSettings([
      ...ALL_TYPES,
      setting({ key: "upload_mb", title: "Предел файла (МБ)", category: "Файлы", type: "integer", value: 50 }),
    ]);
    openGeneralTab();

    expect(screen.getByTestId("workspace-settings-categories")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-settings-category-Сессии")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-settings-category-Файлы")).toBeInTheDocument();
    // Активна первая категория: ключ второй не показан.
    expect(screen.getByTestId("setting-field-session_days")).toBeInTheDocument();
    expect(screen.queryByTestId("setting-field-upload_mb")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("workspace-settings-category-Файлы"));
    expect(screen.getByTestId("setting-field-upload_mb")).toBeInTheDocument();
    expect(screen.queryByTestId("setting-field-session_days")).not.toBeInTheDocument();
  });

  it("показывает состояние загрузки", () => {
    mockSettings([], { isLoading: true });
    openGeneralTab();

    expect(screen.getByText("Загрузка настроек…")).toBeInTheDocument();
  });

  it("показывает ошибку загрузки", () => {
    mockSettings([], { isError: true });
    openGeneralTab();

    expect(screen.getByText("Не удалось загрузить настройки.")).toBeInTheDocument();
  });
});
