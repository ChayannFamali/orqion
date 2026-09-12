import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { DiagnosticsPage } from "../pages/DiagnosticsPage";
import { useEnvironmentDiagnostics } from "../hooks/useDiagnostics";
import type {
  EnvironmentDiagnosticsResponse,
  ExternalServiceDiagnostics,
  HostDiagnostics,
  LocalComponentDiagnostics,
} from "../api/types";

/**
 * Диагностика окружения — только чтение (T-444, T-511).
 *
 * Приёмка T-444: graceful «недоступно» без nvidia-smi; метрики читаются;
 * никаких действий (кнопок «скачать»/«установить») на странице нет.
 *
 * Приёмка T-511: хост и свободное место, статус внешних сервисов по
 * накопленному результату зонда (включая «ещё не проверялся» как отдельное
 * состояние) и локальные компоненты, где «не используется» отличается от
 * «недоступно».
 */

vi.mock("../hooks/useDiagnostics", () => ({
  useEnvironmentDiagnostics: vi.fn(),
}));

function mockHook(data?: EnvironmentDiagnosticsResponse) {
  vi.mocked(useEnvironmentDiagnostics).mockReturnValue({
    data,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useEnvironmentDiagnostics>);
}

const HOST: HostDiagnostics = {
  os_name: "Windows",
  os_version: "11",
  python_version: "3.12.4",
  started_at: "2026-09-12T10:00:00Z",
  uptime_seconds: 3725,
  disks: [
    {
      label: "Хранилище документов",
      path: "H:\\data\\blobs",
      measured_path: null,
      available: true,
      reason: null,
      free_bytes: 120 * 1024 ** 3,
      total_bytes: 500 * 1024 ** 3,
    },
    {
      label: "Векторный индекс",
      path: "H:\\data\\vec.db",
      measured_path: null,
      available: true,
      reason: null,
      free_bytes: 120 * 1024 ** 3,
      total_bytes: 500 * 1024 ** 3,
    },
    {
      label: "База данных",
      path: "H:\\data\\orqion.db",
      measured_path: null,
      available: true,
      reason: null,
      free_bytes: 120 * 1024 ** 3,
      total_bytes: 500 * 1024 ** 3,
    },
  ],
};

const SERVICES: ExternalServiceDiagnostics[] = [
  {
    id: "prov-1",
    kind: "openai",
    role: "llm",
    base_url: "http://127.0.0.1:1234/v1",
    status: "ok",
    last_probe_at: "2026-09-12T09:55:00Z",
    reason: null,
    model_count: 3,
    available_model_count: 2,
  },
  {
    id: "prov-2",
    kind: "openai",
    role: "embedder",
    base_url: "http://127.0.0.1:1235/v1",
    status: "never_probed",
    last_probe_at: null,
    reason: "Зонд ещё не выполнялся: планировщик спит перед первым прогоном (интервал 900 с)",
    model_count: 0,
    available_model_count: 0,
  },
];

const COMPONENTS: LocalComponentDiagnostics[] = [
  {
    name: "Эмбеддинги (локальный пакет)",
    available: true,
    reason: null,
    detail: "Модель BAAI/bge-m3; веса загружаются при первом обращении",
  },
  {
    name: "Реранкинг (локальный пакет)",
    available: false,
    reason: "Пакет FlagEmbedding не установлен — поиск работает без переранжирования",
    detail: "Штатная деградация: выдача остаётся в порядке гибридного поиска",
  },
  {
    name: "Векторное хранилище",
    available: null,
    reason: "Файл ещё не создан — появится при первой индексации",
    detail: "H:\\data\\vec.db",
  },
];

const FULL_DATA: EnvironmentDiagnosticsResponse = {
  nvidia: {
    available: true,
    reason: null,
    driver_version: "551.86",
    gpus: [
      {
        name: "NVIDIA GeForce RTX 4090",
        memory_used_mib: 1024,
        memory_total_mib: 24564,
        temperature_c: 45,
        utilization_percent: 12,
      },
      {
        name: "NVIDIA RTX A6000",
        memory_used_mib: null,
        memory_total_mib: 49140,
        temperature_c: null,
        utilization_percent: 0,
      },
    ],
  },
  vendor_url: "https://www.nvidia.com/en-us/drivers/",
  host: HOST,
  services: SERVICES,
  components: COMPONENTS,
};

const NO_GPU: EnvironmentDiagnosticsResponse = {
  nvidia: {
    available: false,
    reason: "nvidia-smi не найден или недоступен",
    driver_version: null,
    gpus: [],
  },
  vendor_url: null,
  host: HOST,
  services: [],
  components: COMPONENTS,
};

describe("DiagnosticsPage (T-444)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("показывает версию драйвера и метрики GPU", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    expect(screen.getByText("551.86")).toBeInTheDocument();
    expect(screen.getByText("NVIDIA GeForce RTX 4090")).toBeInTheDocument();
    expect(screen.getByText("1024 / 24564 MiB")).toBeInTheDocument();
    expect(screen.getByText("45 °C")).toBeInTheDocument();
    expect(screen.getByText("12%")).toBeInTheDocument();
  });

  it("нечитаемая метрика — «недоступно», не падение", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    // Вторая GPU: memory_used=null, temperature=null
    expect(screen.getAllByText("недоступно").length).toBeGreaterThanOrEqual(2);
  });

  it("нет ссылки вендора — блок не показывается", () => {
    mockHook({ ...FULL_DATA, vendor_url: null });
    render(<DiagnosticsPage />);

    expect(screen.queryByText("Страница драйверов NVIDIA")).not.toBeInTheDocument();
  });

  it("без nvidia-smi — честное «недоступно» с причиной", () => {
    mockHook(NO_GPU);
    render(<DiagnosticsPage />);

    const unavailable = screen.getByTestId("diagnostics-unavailable");
    expect(unavailable.textContent).toContain("Недоступно");
    expect(unavailable.textContent).toContain("nvidia-smi не найден или недоступен");
    expect(screen.queryByTestId("diagnostics-nvidia-available")).not.toBeInTheDocument();
  });

  it("страница только читает: нет кнопок и слов «скачать»/«установить»", () => {
    mockHook(FULL_DATA);
    const { container } = render(<DiagnosticsPage />);

    expect(container.querySelectorAll("button")).toHaveLength(0);
    expect(screen.queryByText(/скачать/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/установить/i)).not.toBeInTheDocument();
  });
});

describe("DiagnosticsPage — хост и диск (T-511)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("показывает ОС, версию Python и время работы", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    const host = screen.getByTestId("diagnostics-host");
    expect(host.textContent).toContain("Windows");
    expect(host.textContent).toContain("3.12.4");
    // 3725 с = 1 ч 2 мин
    expect(host.textContent).toContain("1 ч 2 мин");
  });

  it("показывает свободное место каждого тома", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    // Подписи томов совпадают с названиями локальных компонентов, поэтому
    // запрос ограничен разделом хоста.
    const host = within(screen.getByTestId("diagnostics-host"));
    expect(host.getByText("Хранилище документов")).toBeInTheDocument();
    expect(host.getByText("Векторный индекс")).toBeInTheDocument();
    expect(host.getByText("База данных")).toBeInTheDocument();
    expect(host.getAllByText("120.0 ГБ из 500.0 ГБ")).toHaveLength(3);
  });

  it("аптайм неизвестен без времени старта — «неизвестно», не ноль", () => {
    mockHook({
      ...FULL_DATA,
      host: { ...HOST, started_at: null, uptime_seconds: null },
    });
    render(<DiagnosticsPage />);

    const host = screen.getByTestId("diagnostics-host");
    expect(host.textContent).toContain("неизвестно");
    expect(host.textContent).not.toContain("0 мин");
  });

  it("том без свободного места показывает причину", () => {
    mockHook({
      ...FULL_DATA,
      host: {
        ...HOST,
        disks: [
          {
            label: "Векторный индекс",
            path: "Q:\\vec.db",
            measured_path: null,
            available: false,
            reason: "Свободное место не читается: том недоступен",
            free_bytes: null,
            total_bytes: null,
          },
        ],
      },
    });
    render(<DiagnosticsPage />);

    // «недоступно» встречается и у локальных компонентов — проверяем том.
    const host = within(screen.getByTestId("diagnostics-host"));
    expect(host.getByText("недоступно")).toBeInTheDocument();
    expect(host.getByText(/том недоступен/)).toBeInTheDocument();
  });

  it("измерение по существующему предку отражено явно", () => {
    mockHook({
      ...FULL_DATA,
      host: {
        ...HOST,
        disks: [
          {
            ...HOST.disks[0],
            path: "H:\\data\\blobs\\2026",
            measured_path: "H:\\data\\blobs",
          },
        ],
      },
    });
    render(<DiagnosticsPage />);

    expect(screen.getByText(/измерено по H:\\data\\blobs/)).toBeInTheDocument();
  });
});

describe("DiagnosticsPage — внешние сервисы (T-511)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("доступный провайдер: роль, адрес, число моделей и время проверки", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    const services = screen.getByTestId("diagnostics-services");
    expect(services.textContent).toContain("доступен");
    expect(services.textContent).toContain("модель");
    expect(services.textContent).toContain("эмбеддинги");
    expect(services.textContent).toContain("http://127.0.0.1:1234/v1");
    expect(services.textContent).toContain("моделей доступно: 2");
    expect(services.textContent).toContain("зарегистрировано: 3");
    expect(services.textContent).toContain("Проверен:");
  });

  it("«ещё не проверялся» — отдельное состояние, а не отказ", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    const services = screen.getByTestId("diagnostics-services");
    expect(services.textContent).toContain("ещё не проверялся");
    expect(services.textContent).toContain("Ещё не проверялся");
    expect(services.textContent).not.toContain("недоступен");
    expect(services.textContent).toContain("планировщик спит");
  });

  it("нет провайдеров — раздел не показывается", () => {
    mockHook(NO_GPU);
    render(<DiagnosticsPage />);

    expect(screen.queryByTestId("diagnostics-services")).not.toBeInTheDocument();
  });

  it("отключённый провайдер показан как отключённый", () => {
    mockHook({
      ...FULL_DATA,
      services: [
        {
          id: "prov-3",
          kind: "openai",
          role: "llm",
          base_url: "http://stub/v1",
          status: "disabled",
          last_probe_at: null,
          reason: "Провайдер отключён — плановый зонд его не проверяет",
          model_count: 0,
          available_model_count: 0,
        },
      ],
    });
    render(<DiagnosticsPage />);

    expect(screen.getByTestId("diagnostics-services").textContent).toContain("отключён");
  });
});

describe("DiagnosticsPage — локальные компоненты (T-511)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("доступно / недоступно / не используется — три разных состояния", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    const components = screen.getByTestId("diagnostics-components");
    expect(components.textContent).toContain("Эмбеддинги (локальный пакет)");
    expect(components.textContent).toContain("доступно");
    expect(components.textContent).toContain("недоступно");
    expect(components.textContent).toContain("не используется");
  });

  it("отсутствие пакета реранкера объяснено как штатная деградация", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    expect(screen.getByText(/без переранжирования/)).toBeInTheDocument();
  });

  it("ещё не созданный файл — «не используется» с причиной, не отказ", () => {
    mockHook(FULL_DATA);
    render(<DiagnosticsPage />);

    expect(screen.getByText(/появится при первой индексации/)).toBeInTheDocument();
  });
});
