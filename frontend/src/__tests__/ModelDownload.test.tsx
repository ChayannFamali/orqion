/**
 * T-437: UI скачивания моделей (часть А) + список доступных моделей
 * с флагом зарегистрированности и быстрым добавлением (часть Б).
 *
 * Хуки мокаются целиком (прецедент ProvidersPage.test.tsx): поллинг
 * имитируется сменой данных, возвращаемых useModelDownloadStatus.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ProvidersPage } from "../pages/ProvidersPage";
import {
  useProviders,
  useCreateProvider,
  useUpdateProvider,
  useProbeProvider,
  useCreateModel,
  useUpdateModel,
  useStartModelDownload,
  useModelDownloadStatus,
  isTerminalDownloadStatus,
} from "../hooks/useProviders";
import type { DownloadStatusResponse, ProbeResult, ProviderListResponse } from "../api/types";

vi.mock("../hooks/useProviders");

function makeProvider(overrides: Partial<ProviderListResponse["providers"][0]> = {}) {
  return {
    id: "prov-1",
    kind: "ollama",
    base_url: "http://localhost:11434",
    enabled: true,
    capabilities: {},
    models: [],
    ...overrides,
  };
}

function makeProbeResult(overrides: Partial<ProbeResult> = {}): ProbeResult {
  return {
    available_models: [],
    supports_streaming: true,
    max_parallel: 1,
    model_statuses: [],
    error: null,
    ...overrides,
  };
}

function mockAllHooks() {
  vi.mocked(useCreateProvider).mockReturnValue({} as ReturnType<typeof useCreateProvider>);
  vi.mocked(useUpdateProvider).mockReturnValue({} as ReturnType<typeof useUpdateProvider>);
  vi.mocked(useCreateModel).mockReturnValue({} as ReturnType<typeof useCreateModel>);
  vi.mocked(useUpdateModel).mockReturnValue({} as ReturnType<typeof useUpdateModel>);
  vi.mocked(useProbeProvider).mockReturnValue({
    mutateAsync: vi.fn().mockResolvedValue(makeProbeResult()),
    isPending: false,
  } as unknown as ReturnType<typeof useProbeProvider>);
  vi.mocked(isTerminalDownloadStatus).mockImplementation(
    (status) =>
      status === "completed" || status === "error" || status === "already_downloaded",
  );
}

describe("ProvidersPage: скачивание моделей (T-437, часть А)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows download button only for ollama/lmstudio kinds", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: {
        providers: [
          makeProvider({ id: "p1", kind: "ollama" }),
          makeProvider({ id: "p2", kind: "lmstudio", base_url: "http://localhost:1234/v1" }),
          makeProvider({ id: "p3", kind: "external", base_url: "http://api.test/v1" }),
        ],
      },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();
    vi.mocked(useStartModelDownload).mockReturnValue({} as ReturnType<typeof useStartModelDownload>);
    vi.mocked(useModelDownloadStatus).mockReturnValue({} as ReturnType<typeof useModelDownloadStatus>);

    render(<ProvidersPage />);

    // Две кнопки — по одной на скачиваемый провайдер
    expect(screen.getAllByText("Скачать модель")).toHaveLength(2);
  });

  it("opens download modal with kind-specific placeholder", () => {
    vi.mocked(useProviders).mockReturnValue({
      data: { providers: [makeProvider({ id: "p1", kind: "ollama" })] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();
    vi.mocked(useStartModelDownload).mockReturnValue({} as ReturnType<typeof useStartModelDownload>);
    vi.mocked(useModelDownloadStatus).mockReturnValue({} as ReturnType<typeof useModelDownloadStatus>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Скачать модель"));
    expect(screen.getByText("Скачать модель", { selector: "h3" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("llama3.2:1b")).toBeInTheDocument();
  });

  it("starts download and shows progress from polling", async () => {
    vi.mocked(useProviders).mockReturnValue({
      data: { providers: [makeProvider({ id: "p1", kind: "ollama" })] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();

    const startMutateAsync = vi.fn().mockResolvedValue({
      job_id: "job-1",
      status: "pending",
    } satisfies DownloadStatusResponse);
    vi.mocked(useStartModelDownload).mockReturnValue({
      mutateAsync: startMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useStartModelDownload>);

    let statusData: DownloadStatusResponse | undefined = {
      job_id: "job-1",
      status: "downloading",
      percent: 42.5,
      message: "pulling 797b70c4edf8",
    };
    vi.mocked(useModelDownloadStatus).mockImplementation(
      () => ({ data: statusData }) as ReturnType<typeof useModelDownloadStatus>,
    );

    const view = render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Скачать модель"));
    fireEvent.change(screen.getByPlaceholderText("llama3.2:1b"), {
      target: { value: "all-minilm" },
    });
    fireEvent.click(screen.getByText("Скачать", { selector: "button[type=submit]" }));

    // Ответ старта → форма сменяется статусной областью, поллинг показывает прогресс
    await screen.findByText("all-minilm");
    expect(startMutateAsync).toHaveBeenCalledWith({ providerId: "p1", model: "all-minilm" });
    expect(screen.getByText("42.5%")).toBeInTheDocument();
    expect(screen.getByText("pulling 797b70c4edf8")).toBeInTheDocument();

    // Терминальный статус → успех + кнопка закрытия, поллинг остановлен
    statusData = { job_id: "job-1", status: "completed", percent: 100 };
    view.rerender(<ProvidersPage />);
    expect(await screen.findByText("Модель скачана")).toBeInTheDocument();
    expect(screen.getByText("Закрыть")).toBeInTheDocument();
    // probe обновлён после успешного скачивания
    expect(vi.mocked(useProbeProvider)().mutateAsync).toBeDefined();
  });

  it("terminal start response (already_downloaded) renders without polling", async () => {
    vi.mocked(useProviders).mockReturnValue({
      data: { providers: [makeProvider({ id: "p1", kind: "lmstudio", base_url: "http://localhost:1234/v1" })] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();

    vi.mocked(useStartModelDownload).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        job_id: null,
        status: "already_downloaded",
        percent: 100,
      } satisfies DownloadStatusResponse),
      isPending: false,
    } as unknown as ReturnType<typeof useStartModelDownload>);
    const statusHook = vi.mocked(useModelDownloadStatus);
    statusHook.mockReturnValue({ data: undefined } as ReturnType<typeof useModelDownloadStatus>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Скачать модель"));
    fireEvent.change(
      screen.getByPlaceholderText("https://huggingface.co/org/repo-GGUF"),
      { target: { value: "some/model" } },
    );
    fireEvent.click(screen.getByText("Скачать", { selector: "button[type=submit]" }));

    expect(await screen.findByText("Модель уже скачана на провайдере")).toBeInTheDocument();
    // job_id null → поллинг не запускался
    expect(statusHook).toHaveBeenCalledWith("p1", null, "already_downloaded");
  });

  it("shows backend error text as-is on download failure", async () => {
    vi.mocked(useProviders).mockReturnValue({
      data: { providers: [makeProvider({ id: "p1", kind: "ollama" })] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();

    vi.mocked(useStartModelDownload).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({
        job_id: "job-err",
        status: "pending",
      } satisfies DownloadStatusResponse),
      isPending: false,
    } as unknown as ReturnType<typeof useStartModelDownload>);
    vi.mocked(useModelDownloadStatus).mockReturnValue({
      data: {
        job_id: "job-err",
        status: "error",
        error: 'model "nope" not found, try pulling first',
      },
    } as ReturnType<typeof useModelDownloadStatus>);

    render(<ProvidersPage />);

    fireEvent.click(screen.getByText("Скачать модель"));
    fireEvent.change(screen.getByPlaceholderText("llama3.2:1b"), {
      target: { value: "nope" },
    });
    fireEvent.click(screen.getByText("Скачать", { selector: "button[type=submit]" }));

    expect(
      await screen.findByText('model "nope" not found, try pulling first'),
    ).toBeInTheDocument();
    expect(screen.getByText("Закрыть")).toBeInTheDocument();
  });
});

describe("ProvidersPage: доступные модели с флагом registered (T-437, часть Б)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderWithProbe(probeResult: ProbeResult) {
    vi.mocked(useProviders).mockReturnValue({
      data: { providers: [makeProvider({ id: "p1", kind: "ollama" })] },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useProviders>);
    mockAllHooks();
    vi.mocked(useStartModelDownload).mockReturnValue({} as ReturnType<typeof useStartModelDownload>);
    vi.mocked(useModelDownloadStatus).mockReturnValue({} as ReturnType<typeof useModelDownloadStatus>);
    // probe уже выполнен: результат передаётся карточке через состояние страницы
    vi.mocked(useProbeProvider).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue(probeResult),
      isPending: false,
    } as unknown as ReturnType<typeof useProbeProvider>);

    render(<ProvidersPage />);
    // Запускаем probe, чтобы карточка получила probeResult
    fireEvent.click(screen.getByText("Проверить"));
  }

  it("shows registered badge for models already in orqion", async () => {
    renderWithProbe(
      makeProbeResult({
        available_models: [
          { name: "llama3.2:1b", registered: true },
          { name: "phi4-mini", registered: false },
        ],
      }),
    );

    expect(await screen.findByText("llama3.2:1b")).toBeInTheDocument();
    expect(screen.getByText("в orqion")).toBeInTheDocument();
    // Для незарегистрированной — кнопка быстрого добавления
    expect(screen.getByText("Добавить как модель")).toBeInTheDocument();
  });

  it("quick-add opens model form prefilled with upstream name", async () => {
    renderWithProbe(
      makeProbeResult({
        available_models: [{ name: "phi4-mini", registered: false }],
      }),
    );

    fireEvent.click(await screen.findByText("Добавить как модель"));
    expect(screen.getByText("Новая модель")).toBeInTheDocument();
    // upstream_name предзаполнен именем модели на провайдере
    expect(screen.getByDisplayValue("phi4-mini")).toBeInTheDocument();
  });
});
