import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DiagnosticsPage } from "../pages/DiagnosticsPage";
import { useEnvironmentDiagnostics } from "../hooks/useDiagnostics";
import type { EnvironmentDiagnosticsResponse } from "../api/types";

/**
 * T-444: диагностика окружения — только чтение.
 *
 * Приёмка: graceful «недоступно» без nvidia-smi; метрики читаются;
 * никаких действий (кнопок «скачать»/«установить») на странице нет.
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
    mockHook({
      nvidia: {
        available: false,
        reason: "nvidia-smi не найден или недоступен",
        driver_version: null,
        gpus: [],
      },
      vendor_url: null,
    });
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
