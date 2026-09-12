import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProfilePage } from "../pages/ProfilePage";
import {
  useUpdateUserPreference,
  useUserPreferences,
} from "../hooks/useUserPreferences";
import { useTheme } from "../hooks/useTheme";
import { toast } from "sonner";
import type { ApiError } from "../api/runtime";
import type { UserPreferenceResponse } from "../api/types";

/**
 * Профиль — личные настройки пользователя (Т-512).
 *
 * Ключевое: состав полей задаёт ответ API (категории становятся блоками),
 * поэтому новый ключ появляется в разделе без правки фронтенда; тема
 * оформления — постоянный блок, её значение на сервер не уходит;
 * машинные значения перечисления не показываются — вместо них подписи из
 * ответа.
 */

vi.mock("../hooks/useUserPreferences", () => ({
  useUserPreferences: vi.fn(),
  useUpdateUserPreference: vi.fn(),
}));
vi.mock("../hooks/useTheme", () => ({
  useTheme: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const updateMutate = vi.fn();
const setTheme = vi.fn();

function preference(overrides: Partial<UserPreferenceResponse>): UserPreferenceResponse {
  return {
    key: "k",
    title: "Заголовок",
    description: "Описание",
    category: "Общие",
    type: "string",
    enum_values: null,
    enum_labels: null,
    value: "",
    source: "default",
    editable: true,
    min: null,
    max: null,
    ...overrides,
  } as UserPreferenceResponse;
}

/** Реальный ключ реестра v1 — способ отправки сообщения. */
const SEND_KEY: UserPreferenceResponse = preference({
  key: "chat_send_key",
  title: "Отправка сообщения",
  description: "Какое сочетание клавиш отправляет сообщение в чате.",
  category: "Чат",
  type: "enum",
  enum_values: ["enter", "shift_enter"],
  enum_labels: {
    enter: "Enter — отправить, Shift+Enter — новая строка",
    shift_enter: "Shift+Enter — отправить, Enter — новая строка",
  },
  value: "enter",
});

/** Второй ключ в другой категории — проверяет построение блоков из ответа. */
const OTHER_KEY: UserPreferenceResponse = preference({
  key: "interface_scale",
  title: "Масштаб интерфейса",
  category: "Оформление интерфейса",
  type: "integer",
  value: 100,
  min: 80,
  max: 150,
});

function mockPreferences(
  preferences: UserPreferenceResponse[],
  opts?: { isLoading?: boolean; isError?: boolean },
) {
  vi.mocked(useUserPreferences).mockReturnValue({
    data: { preferences },
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
  } as unknown as ReturnType<typeof useUserPreferences>);
  vi.mocked(useUpdateUserPreference).mockReturnValue({
    mutate: updateMutate,
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useUpdateUserPreference>);
}

function mockTheme(theme: "light" | "dark" = "light") {
  vi.mocked(useTheme).mockReturnValue({
    theme,
    toggle: vi.fn(),
    setTheme,
  } as unknown as ReturnType<typeof useTheme>);
}

describe("Профиль — личные настройки (Т-512)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockTheme();
  });

  it("рисует поле по описанию из ответа: тип, опции, заголовок", () => {
    mockPreferences([SEND_KEY]);
    render(<ProfilePage />);

    const select = screen.getByTestId("setting-field-chat_send_key");
    expect(select.tagName).toBe("SELECT");
    expect(Array.from(select.querySelectorAll("option")).map((o) => o.value)).toEqual([
      "enter",
      "shift_enter",
    ]);
    expect(select).toHaveValue("enter");
    expect(screen.getByText("Отправка сообщения")).toBeInTheDocument();
    expect(screen.getByText("Какое сочетание клавиш отправляет сообщение в чате.")).toBeInTheDocument();
  });

  it("подписи вариантов — из ответа, машинные значения не показываются", () => {
    mockPreferences([SEND_KEY]);
    render(<ProfilePage />);

    const options = Array.from(
      screen.getByTestId("setting-field-chat_send_key").querySelectorAll("option"),
    );
    expect(options.map((o) => o.textContent)).toEqual([
      "Enter — отправить, Shift+Enter — новая строка",
      "Shift+Enter — отправить, Enter — новая строка",
    ]);
    // Машинное значение остаётся значением option, но не его текстом.
    expect(options.map((o) => o.value)).toEqual(["enter", "shift_enter"]);
  });

  it("категории становятся блоками: состав из ответа, не из кода", () => {
    mockPreferences([SEND_KEY, OTHER_KEY]);
    render(<ProfilePage />);

    expect(screen.getByTestId("profile-category-Чат")).toBeInTheDocument();
    expect(screen.getByTestId("profile-category-Оформление интерфейса")).toBeInTheDocument();
    expect(screen.getByTestId("setting-field-chat_send_key")).toBeInTheDocument();
    expect(screen.getByTestId("setting-field-interface_scale")).toBeInTheDocument();
  });

  it("числовое поле берёт границы из ответа и уходит на сервер при потере фокуса", () => {
    mockPreferences([OTHER_KEY]);
    render(<ProfilePage />);

    const input = screen.getByTestId("setting-field-interface_scale");
    expect(input).toHaveAttribute("min", "80");
    expect(input).toHaveAttribute("max", "150");
    expect(input).toHaveAttribute("step", "1");

    fireEvent.change(input, { target: { value: "125" } });
    fireEvent.blur(input);

    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0]).toEqual({ key: "interface_scale", value: 125 });
  });

  it("метка источника: «по умолчанию» и «изменено»", () => {
    mockPreferences([SEND_KEY, { ...OTHER_KEY, source: "db" as const }]);
    render(<ProfilePage />);

    expect(screen.getByTestId("setting-source-chat_send_key")).toHaveTextContent(
      "по умолчанию",
    );
    expect(screen.getByTestId("setting-source-interface_scale")).toHaveTextContent("изменено");
  });

  it("личная настройка доступна на изменение: поле не заблокировано", () => {
    // Доступ — владение строкой, а не способность роли: сервер отдаёт
    // editable=true всем, поэтому заблокированных полей в разделе нет.
    mockPreferences([SEND_KEY, OTHER_KEY]);
    render(<ProfilePage />);

    expect(screen.getByTestId("setting-field-chat_send_key")).not.toBeDisabled();
    expect(screen.getByTestId("setting-field-interface_scale")).not.toBeDisabled();
    expect(screen.queryAllByTestId(/^setting-readonly-/)).toHaveLength(0);
  });

  it("выбор варианта уходит на сервер сразу и показывает успех", () => {
    mockPreferences([SEND_KEY]);
    updateMutate.mockImplementation((_body, handlers) => handlers?.onSuccess?.());
    render(<ProfilePage />);

    fireEvent.change(screen.getByTestId("setting-field-chat_send_key"), {
      target: { value: "shift_enter" },
    });

    expect(updateMutate).toHaveBeenCalledWith(
      { key: "chat_send_key", value: "shift_enter" },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
    expect(toast.success).toHaveBeenCalledWith("Настройка сохранена");
  });

  it("не отправляет запрос, если значение не изменилось", () => {
    mockPreferences([OTHER_KEY]);
    render(<ProfilePage />);

    const input = screen.getByTestId("setting-field-interface_scale");
    fireEvent.change(input, { target: { value: "100" } });
    fireEvent.blur(input);

    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("отказ сервера показывается инлайн, черновик возвращается к значению сервера", () => {
    mockPreferences([SEND_KEY]);
    const apiError: ApiError = {
      error: "setting_value_invalid",
      reason: "Значение настройки не соответствует её описанию",
      constraint: { key: "chat_send_key", expected: "enum" },
      hint: "Ожидается одно из значений списка: enter, shift_enter",
    };
    updateMutate.mockImplementation((_body, handlers) => handlers?.onError?.(apiError));
    render(<ProfilePage />);

    fireEvent.change(screen.getByTestId("setting-field-chat_send_key"), {
      target: { value: "shift_enter" },
    });

    expect(screen.getByTestId("setting-error-chat_send_key")).toHaveTextContent(
      "Ожидается одно из значений списка: enter, shift_enter",
    );
    expect(screen.getByTestId("setting-field-chat_send_key")).toHaveValue("enter");
  });

  it("ошибка без подсказки показывает причину", () => {
    mockPreferences([SEND_KEY]);
    updateMutate.mockImplementation((_body, handlers) =>
      handlers?.onError?.({
        error: "setting_value_invalid",
        reason: "Не удалось сохранить значение",
        constraint: null,
        hint: null,
      } satisfies ApiError),
    );
    render(<ProfilePage />);

    fireEvent.change(screen.getByTestId("setting-field-chat_send_key"), {
      target: { value: "shift_enter" },
    });

    expect(screen.getByTestId("setting-error-chat_send_key")).toHaveTextContent(
      "Не удалось сохранить значение",
    );
  });

  it("тема выбирается из двух вариантов и не уходит на сервер", () => {
    mockPreferences([SEND_KEY]);
    render(<ProfilePage />);

    const themeField = screen.getByTestId("theme-field");
    expect(themeField).toHaveValue("light");
    expect(Array.from(themeField.querySelectorAll("option")).map((o) => o.textContent)).toEqual([
      "Светлая",
      "Тёмная",
    ]);

    fireEvent.change(themeField, { target: { value: "dark" } });

    expect(setTheme).toHaveBeenCalledWith("dark");
    // Тема живёт в браузере: в запросе личных настроек её нет.
    expect(updateMutate).not.toHaveBeenCalled();
  });

  it("текущая тема отражается в поле выбора", () => {
    mockTheme("dark");
    mockPreferences([SEND_KEY]);
    render(<ProfilePage />);

    expect(screen.getByTestId("theme-field")).toHaveValue("dark");
  });

  it("неизвестное значение темы трактуется как светлая", () => {
    // Защита от испорченного localStorage: поле не остаётся пустым.
    mockPreferences([SEND_KEY]);
    vi.mocked(useTheme).mockReturnValue({
      theme: "sepia" as unknown as "light",
      toggle: vi.fn(),
      setTheme,
    } as unknown as ReturnType<typeof useTheme>);
    render(<ProfilePage />);

    expect(screen.getByTestId("theme-field")).toHaveValue("light");
    fireEvent.change(screen.getByTestId("theme-field"), { target: { value: "dark" } });
    expect(setTheme).toHaveBeenCalledWith("dark");
  });

  it("показывает состояние загрузки", () => {
    mockPreferences([], { isLoading: true });
    render(<ProfilePage />);

    expect(screen.getByText("Загрузка настроек…")).toBeInTheDocument();
    // Тема не зависит от загрузки настроек.
    expect(screen.getByTestId("theme-field")).toBeInTheDocument();
  });

  it("показывает ошибку загрузки и оставляет тему доступной", () => {
    mockPreferences([], { isError: true });
    render(<ProfilePage />);

    expect(screen.getByTestId("profile-preferences-error")).toHaveTextContent(
      "Не удалось загрузить личные настройки.",
    );
    expect(screen.getByTestId("theme-field")).toBeInTheDocument();
  });

  it("при пустом реестре показывает сообщение без блоков настроек", () => {
    mockPreferences([]);
    render(<ProfilePage />);

    expect(screen.getByTestId("profile-preferences-empty")).toHaveTextContent(
      "Личных настроек пока нет.",
    );
    expect(screen.queryAllByTestId(/^profile-category-/)).toHaveLength(0);
  });

  it("в текстах раздела нет внутренней документации", () => {
    mockPreferences([SEND_KEY, OTHER_KEY]);
    const { container } = render(<ProfilePage />);

    for (const token of ["ADR", "arch.md", "planning.md", "§", "Т-5"]) {
      expect(container.textContent).not.toContain(token);
    }
  });

  it("на каждую категорию ровно один блок, ключи не дублируются", () => {
    mockPreferences([SEND_KEY, OTHER_KEY]);
    render(<ProfilePage />);

    expect(screen.getAllByTestId("profile-category-Чат")).toHaveLength(1);
    expect(screen.getAllByTestId("profile-category-Оформление интерфейса")).toHaveLength(1);
    expect(screen.getAllByTestId(/^setting-block-/)).toHaveLength(2);
  });
});
