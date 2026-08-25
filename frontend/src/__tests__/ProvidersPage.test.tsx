import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProvidersPage } from "../pages/ProvidersPage";
import { useProviders, useCreateProvider, useUpdateProvider, useProbeProvider, useCreateModel, useUpdateModel, useDeleteProvider } from "../hooks/useProviders";
import type { ProviderListResponse } from "../api/types";

vi.mock("../hooks/useProviders");

function makeProvider(overrides: Partial<ProviderListResponse["providers"][0]> = {}) {
  return {
    id: "prov-1",
    kind: "openai",
    base_url: "http://127.0.0.1:1234/v1",
    enabled: true,
    capabilities: {},
    models: [],
    ...overrides,
  };
}

function mockProvidersResponse(providers: ProviderListResponse["providers"]): ProviderListResponse {
  return { providers };
}

describe("ProvidersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders provider list with kind and base_url", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([
        makeProvider({ id: "p1", kind: "openai", base_url: "http://localhost:1234/v1" }),
        makeProvider({ id: "p2", kind: "lmstudio", base_url: "http://localhost:5678/v1", enabled: false }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:1234/v1")).toBeInTheDocument();
    expect(screen.getByText("lmstudio")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:5678/v1")).toBeInTheDocument();
  });

  it("shows empty state when no providers", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    expect(screen.getByText("Нет провайдеров")).toBeInTheDocument();
  });

  it("shows error state on error", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("fetch failed"),
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    expect(screen.getByText("Ошибка загрузки провайдеров")).toBeInTheDocument();
  });

  it("shows loading spinner", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    const { container } = render(<ProvidersPage />);

    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("opens create form when Add button clicked", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Добавить"));
    expect(screen.getByText("Новый провайдер")).toBeInTheDocument();
    // T-437: kind — select с каноническим набором, не свободный ввод
    const kindSelect = screen.getByRole("combobox");
    expect(kindSelect).toHaveValue("external");
    expect(screen.getByRole("option", { name: "ollama" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "lmstudio" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "external" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("sk-...")).toBeInTheDocument();
  });

  it("create form has write-only api_key field with security note", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Добавить"));
    const keyInput = screen.getByPlaceholderText("sk-...");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(screen.getByText(/Ключ шифруется и не отображается после сохранения/)).toBeInTheDocument();
  });

  it("edit form has write-only api_key rotation field, empty by default", () => {
    const provider = makeProvider({ id: "p1" });
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([provider]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Изменить"));
    expect(screen.getByText("Изменить провайдер")).toBeInTheDocument();
    const keyInput = screen.getByPlaceholderText("Оставьте пустым, чтобы не менять");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(keyInput).toHaveValue("");
    expect(screen.getByText(/Текущий ключ не отображается/)).toBeInTheDocument();
  });

  it("shows enabled/disabled status badge", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([
        makeProvider({ id: "p1", enabled: true }),
        makeProvider({ id: "p2", enabled: false }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    expect(screen.getByText("включён")).toBeInTheDocument();
    expect(screen.getByText("отключён")).toBeInTheDocument();
  });

  it("opens model create form when Add Model clicked", () => {
    const provider = makeProvider({ id: "p1", models: [] });
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([provider]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Добавить модель"));
    expect(screen.getByText("Новая модель")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("my-model")).toBeInTheDocument();
  });

  it("shows model create button when provider has no models", () => {
    const provider = makeProvider({ id: "p1", models: [] });
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([provider]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    expect(screen.getByText("Добавить модель")).toBeInTheDocument();
  });

  it("edit model modal stays open on duplicate alias error", async () => {
    const model = {
      id: "m1",
      alias: "model-a",
      upstream_name: "test",
      locality: "local",
      max_input_tokens: null,
      max_output_tokens: null,
      supports_reasoning: false,
      reasoning_toggleable: false,
      cost_in: null,
      cost_out: null,
      enabled: true,
    };
    const provider = makeProvider({ id: "p1", models: [model] });
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([provider]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue({
        error: "bad_request",
        reason: "Алиас модели должен быть уникален в рамках workspace",
        hint: "Алиас 'model-b' уже существует",
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateModel>);

    render(<ProvidersPage />);

    // Открываем форму редактирования модели (кнопка настроек в строке модели)
    const editModelBtn = screen.getByLabelText("Изменить модель");
    fireEvent.click(editModelBtn);

    // Ждём появления модалки
    expect(screen.getByText("Изменить модель")).toBeInTheDocument();

    // Меняем alias на дубликат
    const aliasInput = screen.getByDisplayValue("model-a");
    fireEvent.change(aliasInput, { target: { value: "model-b" } });

    // Отправляем форму
    const saveButton = screen.getByText("Сохранить");
    await fireEvent.click(saveButton);

    // Модалка остаётся открытой (не закрылась из-за ошибки)
    expect(screen.getByText("Изменить модель")).toBeInTheDocument();
  });

  it("opens delete provider modal and calls delete mutation", async () => {
    const provider = makeProvider({ id: "p1", kind: "ollama" });
    vi.mocked(useProviders).mockReturnValue({
      data: mockProvidersResponse([provider]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
    vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
    vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
    vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
    vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);
    const mutateAsync = vi.fn().mockResolvedValue({ deleted: true });
    vi.mocked(useDeleteProvider).mockReturnValue({
      isPending: false,
      mutateAsync,
    } as unknown as ReturnType<typeof useDeleteProvider>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByLabelText("Удалить провайдер"));
    expect(screen.getByText("Удалить провайдер")).toBeInTheDocument();
    expect(
      screen.getByText(/Возможно только при отсутствии зарегистрированных моделей/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText("Удалить", { selector: "button" }));
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("p1");
    });
  });
});
