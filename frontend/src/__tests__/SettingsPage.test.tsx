import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsPage } from "../pages/SettingsPage";
import { useRagSettings, useUpdateRagSettings } from "../hooks/useRagSettings";
import { toast } from "sonner";

/**
 * T-506: настройки RAG-поиска уровня рабочей области.
 *
 * Приёмка: чтение для всех; изменение только с правом управления корпусами;
 * формулировки «Порог релевантности после реранкинга» и «Максимум фрагментов
 * контекста»; пресеты — подсказки, не кнопки; валидация диапазонов.
 */

vi.mock("../hooks/useRagSettings", () => ({
  useRagSettings: vi.fn(),
  useUpdateRagSettings: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mutateAsync = vi.fn();

function mockHooks(
  data?: { relevance_threshold: number; max_fragments: number },
  opts?: { isLoading?: boolean; isError?: boolean },
) {
  vi.mocked(useRagSettings).mockReturnValue({
    data,
    isLoading: opts?.isLoading ?? false,
    isError: opts?.isError ?? false,
  } as unknown as ReturnType<typeof useRagSettings>);
  vi.mocked(useUpdateRagSettings).mockReturnValue({
    mutate: mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateRagSettings>);
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
    mockHooks({ relevance_threshold: 40, max_fragments: 5 });
    render(<SettingsPage capabilities={ADMIN} />);

    expect(screen.getByTestId("rag-threshold-input")).toHaveValue(40);
    expect(screen.getByTestId("rag-max-fragments-input")).toHaveValue(5);
  });

  it("для роли без права управления корпусами — только чтение", () => {
    mockHooks({ relevance_threshold: 0, max_fragments: 8 });
    render(<SettingsPage capabilities={USER} />);

    expect(screen.getByTestId("rag-threshold-input")).toBeDisabled();
    expect(screen.getByTestId("rag-max-fragments-input")).toBeDisabled();
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
      { relevance_threshold: 50, max_fragments: 4 },
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

  it("показывает сообщение при ошибке загрузки", () => {
    mockHooks(undefined, { isError: true });
    render(<SettingsPage capabilities={ADMIN} />);

    expect(screen.getByText("Не удалось загрузить настройки поиска.")).toBeInTheDocument();
  });
});
