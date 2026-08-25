import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ProvidersPage } from "../pages/ProvidersPage";
import {
  useProviders,
  useCreateProvider,
  useUpdateProvider,
  useProbeProvider,
  useCreateModel,
  useUpdateModel,
  useDeleteModel,
} from "../hooks/useProviders";
import type { ModelResponse, ProviderListResponse } from "../api/types";

vi.mock("../hooks/useProviders");

function makeModel(overrides: Partial<ModelResponse> = {}): ModelResponse {
  return {
    id: "m1",
    alias: "model-a",
    upstream_name: "upstream-a",
    locality: "local",
    max_input_tokens: null,
    max_output_tokens: null,
    supports_reasoning: false,
    reasoning_toggleable: false,
    cost_in: null,
    cost_out: null,
    enabled: true,
    ...overrides,
  };
}

function makeProvider(
  overrides: Partial<ProviderListResponse["providers"][0]> = {},
): ProviderListResponse["providers"][0] {
  return {
    id: "prov-1",
    kind: "external",
    base_url: "http://api.test/v1",
    enabled: true,
    capabilities: {},
    models: [],
    ...overrides,
  };
}

function mockHooks() {
  vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
  vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
  vi.mocked(useProbeProvider).mockReturnValue({} as ReturnType<typeof useProbeProvider>);
  vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
  vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);
}

function renderWithModels(models: ModelResponse[], kind = "external") {
  vi.mocked(useProviders).mockReturnValue({
    data: { providers: [makeProvider({ kind, models })] },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useProviders>);
  mockHooks();
  return render(<ProvidersPage />);
}

describe("ProvidersPage: удаление модели (T-443, коммит 2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("кнопка удаления видна для каждой модели", () => {
    vi.mocked(useDeleteModel).mockReturnValue({} as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel(), makeModel({ id: "m2", alias: "model-b" })]);
    expect(screen.getAllByLabelText("Удалить модель")).toHaveLength(2);
  });

  it("открывает модалку подтверждения по клику на корзину", () => {
    vi.mocked(useDeleteModel).mockReturnValue({} as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()]);
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    expect(screen.getByText("Удалить модель")).toBeInTheDocument();
    // Модалка спрашивает подтверждение с именем модели
    expect(screen.getByText(/из orqion\?/)).toBeInTheDocument();
    expect(screen.getAllByText("model-a").length).toBeGreaterThanOrEqual(2);
  });

  it("для external-провайдера нет чекбокса очистки с диска", () => {
    vi.mocked(useDeleteModel).mockReturnValue({} as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()], "external");
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    expect(screen.queryByText(/удалить файл с диска/)).not.toBeInTheDocument();
  });

  it("для ollama-провайдера чекбокс очистки с диска доступен (по умолчанию выключен)", () => {
    vi.mocked(useDeleteModel).mockReturnValue({} as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()], "ollama");
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).toBeInTheDocument();
    expect(checkbox).not.toBeChecked();
  });

  it("подтверждение без очистки диска вызывает мутацию с deleteFromDisk=false", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue({ deleted: true, disk_deleted: null, disk_error: null });
    vi.mocked(useDeleteModel).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()], "ollama");
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    // Кнопка «Удалить» в модалке (не корзина)
    const confirmButtons = screen.getAllByText("Удалить");
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ modelId: "m1", deleteFromDisk: false });
    });
  });

  it("чекбокс очистки диска передаёт deleteFromDisk=true", async () => {
    const mutateAsync = vi
      .fn()
      .mockResolvedValue({ deleted: true, disk_deleted: true, disk_error: null });
    vi.mocked(useDeleteModel).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()], "ollama");
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    fireEvent.click(screen.getByRole("checkbox"));
    const confirmButtons = screen.getAllByText("Удалить");
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ modelId: "m1", deleteFromDisk: true });
    });
  });

  it("ошибка очистки диска показывается, модалка не закрывается", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      deleted: true,
      disk_deleted: false,
      disk_error: "Ollama вернул HTTP 500: boom",
    });
    vi.mocked(useDeleteModel).mockReturnValue({
      mutateAsync,
      isPending: false,
      isError: false,
    } as unknown as ReturnType<typeof useDeleteModel>);
    renderWithModels([makeModel()], "ollama");
    fireEvent.click(screen.getByLabelText("Удалить модель"));
    fireEvent.click(screen.getByRole("checkbox"));
    const confirmButtons = screen.getAllByText("Удалить");
    fireEvent.click(confirmButtons[confirmButtons.length - 1]);
    await waitFor(() => {
      expect(screen.getByText(/Ollama вернул HTTP 500/)).toBeInTheDocument();
    });
    // Метаданные удалены — это тоже показано явно
    expect(screen.getByText(/удалена из orqion/)).toBeInTheDocument();
  });
});
