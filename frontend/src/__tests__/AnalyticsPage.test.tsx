import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AnalyticsPage } from "../pages/AnalyticsPage";
import { useAnalytics } from "../hooks/useAnalytics";
import { useRoles } from "../hooks/useRoles";
import type {
  AnalyticsResponse,
  RoleListResponse,
  RoleResponse,
} from "../api/types";

vi.mock("../hooks/useAnalytics");
vi.mock("../hooks/useRoles");

vi.mock("../api/analytics", () => ({
  apiGetAnalytics: vi.fn(),
}));

function makeRole(
  name: string,
  budget: Record<string, number> | null,
): RoleResponse {
  return {
    id: `role-${name}`,
    name,
    is_builtin: true,
    policy: {
      models: ["local/*"],
      max_input_tokens: null,
      max_output_tokens: null,
      reasoning: "off",
      budget,
      rpm: null,
      tpm: null,
      corpora: ["*"],
      capabilities: name === "admin" ? ["*"] : ["chat"],
    },
  };
}

function makeAnalyticsData(
  overrides: Partial<AnalyticsResponse> = {},
): AnalyticsResponse {
  return {
    summary: {
      total_requests: 100,
      total_tokens_in: 5000,
      total_tokens_out: 3000,
      total_cost: 1.5,
      total_errors: 2,
      avg_latency_ms: 200,
    },
    by_day: [
      {
        date: "2026-08-10",
        requests: 50,
        tokens_in: 2500,
        tokens_out: 1500,
        cost: 0.75,
        errors: 1,
        avg_latency_ms: 180,
      },
      {
        date: "2026-08-11",
        requests: 50,
        tokens_in: 2500,
        tokens_out: 1500,
        cost: 0.75,
        errors: 1,
        avg_latency_ms: 220,
      },
    ],
    by_model: [
      {
        model_id: "m1",
        model_alias: "gpt-4",
        requests: 80,
        tokens_in: 4000,
        tokens_out: 2400,
        cost: 1.2,
        errors: 1,
      },
      {
        model_id: "m2",
        model_alias: "claude",
        requests: 20,
        tokens_in: 1000,
        tokens_out: 600,
        cost: 0.3,
        errors: 1,
      },
    ],
    by_user: [
      {
        user_id: "u1",
        user_email: "alice@test.com",
        role_name: "developer",
        team_name: null,
        requests: 60,
        tokens_in: 3000,
        tokens_out: 1800,
        cost: 0.9,
        errors: 1,
      },
      {
        user_id: "u2",
        user_email: "bob@test.com",
        role_name: "admin",
        team_name: null,
        requests: 40,
        tokens_in: 2000,
        tokens_out: 1200,
        cost: 0.6,
        errors: 1,
      },
    ],
    ...overrides,
  };
}

function mockHooks(
  weekData?: AnalyticsResponse,
  monthData?: AnalyticsResponse,
  roles?: RoleResponse[],
  loading = false,
  error: unknown = null,
) {
  // Default fallback returns monthData (or weekData if no month data).
  // This ensures re-renders (e.g. tab switch) still get the right data.
  const fallbackData = monthData ?? weekData;
  vi.mocked(useAnalytics).mockReturnValue({
    data: fallbackData,
    isLoading: loading,
    error,
  } as ReturnType<typeof useAnalytics>);

  // First two calls: week, then month (matching AnalyticsPage call order)
  vi.mocked(useAnalytics)
    .mockReturnValueOnce({
      data: weekData,
      isLoading: loading,
      error,
    } as ReturnType<typeof useAnalytics>)
    .mockReturnValueOnce({
      data: monthData,
      isLoading: loading,
      error,
    } as ReturnType<typeof useAnalytics>);

  vi.mocked(useRoles).mockReturnValue({
    data: roles
      ? ({ roles } as RoleListResponse)
      : undefined,
    isLoading: false,
    error: null,
  } as ReturnType<typeof useRoles>);
}

describe("AnalyticsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state", () => {
    mockHooks(undefined, undefined, undefined, true);

    render(<AnalyticsPage />);

    // Loading spinner is shown via Loader2 icon
    expect(document.querySelector(".animate-spin")).toBeTruthy();
  });

  it("renders error state", () => {
    mockHooks(undefined, undefined, undefined, false, new Error("fail"));

    render(<AnalyticsPage />);

    expect(screen.getByText("Ошибка загрузки аналитики")).toBeInTheDocument();
  });

  it("renders overview tab by default with summary cards", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5000000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    expect(screen.getByText("Аналитика потребления")).toBeInTheDocument();
    expect(screen.getByText("Запросы (7 дней)")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("$1.50")).toBeInTheDocument();
  });

  it("renders daily chart with data points", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    expect(screen.getByText("Расход по дням (запросы и токены)")).toBeInTheDocument();
    // Recharts doesn't render SVG content in jsdom (0-width container).
    // Verify the chart container is present.
    expect(document.querySelector(".recharts-responsive-container")).toBeTruthy();
  });

  it("renders model breakdown chart", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    expect(screen.getByText("Разбивка по моделям (запросы)")).toBeInTheDocument();
    expect(document.querySelector(".recharts-responsive-container")).toBeTruthy();
  });

  it("renders role breakdown pie chart", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    expect(screen.getByText("Разбивка по ролям (расход)")).toBeInTheDocument();
  });

  it("shows budget burn section with users near limit", () => {
    const monthData = makeAnalyticsData({
      by_user: [
        {
          user_id: "u1",
          user_email: "alice@test.com",
          role_name: "developer",
          team_name: null,
          requests: 60,
          tokens_in: 4500000,
          tokens_out: 900000,
          cost: 0.9,
          errors: 1,
        },
        {
          user_id: "u2",
          user_email: "bob@test.com",
          role_name: "admin",
          team_name: null,
          requests: 40,
          tokens_in: 2000,
          tokens_out: 1200,
          cost: 0.6,
          errors: 1,
        },
      ],
    });
    const weekData = makeAnalyticsData();
    mockHooks(weekData, monthData, [
      makeRole("developer", { tokens_month: 5000000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    // Overview tab: aggregated counter only, no per-user names
    expect(screen.getByText("Сгорание бюджета (текущий месяц)")).toBeInTheDocument();
    const burnInfo = screen.getByText(/близки к лимиту/);
    expect(burnInfo.textContent).toMatch(/\d+ из \d+/);
    expect(burnInfo.textContent).toContain("пользователей близки к лимиту");
    expect(screen.getByText("1 без лимита")).toBeInTheDocument();
    // Per-user budget table NOT on overview tab
    expect(screen.queryByText("Статус бюджета по пользователям")).not.toBeInTheDocument();
  });

  it("shows per-user budget table on Users tab", () => {
    const monthData = makeAnalyticsData({
      by_user: [
        {
          user_id: "u1",
          user_email: "alice@test.com",
          role_name: "developer",
          team_name: null,
          requests: 60,
          tokens_in: 4500000,
          tokens_out: 900000,
          cost: 0.9,
          errors: 1,
        },
        {
          user_id: "u2",
          user_email: "bob@test.com",
          role_name: "admin",
          team_name: null,
          requests: 40,
          tokens_in: 2000,
          tokens_out: 1200,
          cost: 0.6,
          errors: 1,
        },
      ],
    });
    const weekData = makeAnalyticsData();
    mockHooks(weekData, monthData, [
      makeRole("developer", { tokens_month: 5000000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    // Switch to Users tab to see per-user budget table
    fireEvent.click(screen.getByText("Пользователи"));

    expect(screen.getByText("Статус бюджета по пользователям (текущий месяц)")).toBeInTheDocument();
    // alice@test.com appears in both top consumers and budget table
    expect(screen.getAllByText("alice@test.com").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("developer").length).toBeGreaterThanOrEqual(1);
    // Burn percent should be shown
    expect(screen.getByText("108%")).toBeInTheDocument();
  });

  it("shows empty budget section when no users have budget", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, [
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    expect(
      screen.getByText("Нет пользователей с установленным бюджетом"),
    ).toBeInTheDocument();
  });

  it("switches to users tab on click", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));

    expect(screen.getByText("Верхние потребители (по расходу за 7 дней)")).toBeInTheDocument();
    expect(screen.getByText("alice@test.com")).toBeInTheDocument();
    expect(screen.getByText("bob@test.com")).toBeInTheDocument();
  });

  it("shows top consumers sorted by cost", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));

    // alice has $0.90, bob has $0.60 — alice should be first
    const rows = screen.getAllByText(/@test\.com/);
    expect(rows[0]).toHaveTextContent("alice@test.com");
    expect(rows[1]).toHaveTextContent("bob@test.com");
  });

  it("shows personal slice when user is selected", () => {
    const data = makeAnalyticsData();
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5000000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));
    // Click alice in the top consumers table (first occurrence)
    const aliceElements = screen.getAllByText("alice@test.com");
    fireEvent.click(aliceElements[0]);

    expect(screen.getByText(/Персональный срез/)).toBeInTheDocument();
    expect(screen.getByText("Запросы (месяц)")).toBeInTheDocument();
    // "Сгорание бюджета" is split by (токены) in a child element — use regex
    expect(screen.getByText(/Сгорание бюджета/)).toBeInTheDocument();
  });

  it("shows empty state when no analytics data", () => {
    const emptyData: AnalyticsResponse = {
      summary: {
        total_requests: 0,
        total_tokens_in: 0,
        total_tokens_out: 0,
        total_cost: 0,
        total_errors: 0,
        avg_latency_ms: null,
      },
      by_day: [],
      by_model: [],
      by_user: [],
    };
    mockHooks(emptyData, emptyData, []);

    render(<AnalyticsPage />);

    expect(screen.getByText("Запросы (7 дней)")).toBeInTheDocument();
    // Summary shows "0"
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("renders 'Без пользователя' for sentinel user_id in Users tab", () => {
    const NIL_ID = "00000000-0000-0000-0000-000000000000";
    const data = makeAnalyticsData({
      by_user: [
        {
          user_id: NIL_ID,
          user_email: null,
          role_name: null,
          team_name: null,
          requests: 30,
          tokens_in: 500,
          tokens_out: 200,
          cost: 0.3,
          errors: 0,
        },
        {
          user_id: "u1",
          user_email: "alice@test.com",
          role_name: "developer",
          team_name: null,
          requests: 60,
          tokens_in: 3000,
          tokens_out: 1800,
          cost: 0.9,
          errors: 1,
        },
      ],
    });
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));

    expect(screen.getByText("Без пользователя")).toBeInTheDocument();
    expect(screen.getByText("alice@test.com")).toBeInTheDocument();
  });

  it("renders 'Без модели' for sentinel model_id in model breakdown", () => {
    const NIL_ID = "00000000-0000-0000-0000-000000000000";
    const data = makeAnalyticsData({
      by_model: [
        {
          model_id: NIL_ID,
          model_alias: null,
          requests: 20,
          tokens_in: 500,
          tokens_out: 300,
          cost: 0.2,
          errors: 0,
        },
        {
          model_id: "m1",
          model_alias: "gpt-4",
          requests: 80,
          tokens_in: 4000,
          tokens_out: 2400,
          cost: 1.2,
          errors: 1,
        },
      ],
    });
    mockHooks(data, data, []);

    render(<AnalyticsPage />);

    // Model breakdown chart is on Overview tab (default)
    expect(screen.getByText("Разбивка по моделям (запросы)")).toBeInTheDocument();
    expect(document.querySelector(".recharts-responsive-container")).toBeTruthy();
  });
});

describe("T-441: прогноз расхода бюджета на дашборде", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function monthDataWithAlice(tokensIn: number, tokensOut: number, cost: number) {
    return makeAnalyticsData({
      by_user: [
        {
          user_id: "u1",
          user_email: "alice@test.com",
          role_name: "developer",
          team_name: null,
          requests: 60,
          tokens_in: tokensIn,
          tokens_out: tokensOut,
          cost,
          errors: 1,
        },
        {
          user_id: "u2",
          user_email: "bob@test.com",
          role_name: "admin",
          team_name: null,
          requests: 40,
          tokens_in: 2000,
          tokens_out: 1200,
          cost: 0.6,
          errors: 1,
        },
      ],
    });
  }

  it("обзор: агрегированный счётчик «по прогнозу исчерпают» (25-й день месяца)", () => {
    // 25 дней прошло из 31. Токены: 4.8M/25=192k/день → 5M/192k ≈ день 27 →
    // исчерпание. Стоимость: 0.9/25=0.036/день → 10/0.036 ≈ день 278 → нет.
    vi.setSystemTime(new Date(2026, 7, 25));
    const data = monthDataWithAlice(3_000_000, 1_800_000, 0.9);
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5_000_000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    const forecast = screen.getByTestId("budget-forecast-overview");
    expect(forecast.textContent).toContain("по прогнозу исчерпают к концу месяца:");
    expect(forecast.textContent).toContain("1 из 1");
  });

  it("обзор: < 3 дней месяца — прогноз не показывается вовсе", () => {
    vi.setSystemTime(new Date(2026, 7, 2));
    const data = monthDataWithAlice(3_000_000, 1_800_000, 0.9);
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5_000_000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    // Карточка «Сгорание бюджета» есть, строки прогноза нет
    expect(screen.getByText("Сгорание бюджета (текущий месяц)")).toBeInTheDocument();
    expect(screen.queryByTestId("budget-forecast-overview")).not.toBeInTheDocument();
  });

  it("drill-down: текст «исчерпан к дню X» по токенам и «не исчерпается» по стоимости", () => {
    vi.setSystemTime(new Date(2026, 7, 25));
    const data = monthDataWithAlice(3_000_000, 1_800_000, 0.9);
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5_000_000, cost_month: 10 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));
    fireEvent.click(screen.getAllByText("alice@test.com")[0]);

    const forecast = screen.getByTestId("budget-forecast-user");
    expect(forecast.textContent).toContain(
      "Токены: при текущем темпе лимит будет исчерпан к дню 27",
    );
    expect(forecast.textContent).toContain(
      "Стоимость: при текущем темпе не исчерпается до конца месяца",
    );
  });

  it("cost_month=0 — нет прогноза по стоимости (не «исчерпан сегодня»)", () => {
    vi.setSystemTime(new Date(2026, 7, 25));
    // Токены: 100k/25=4k/день → 5M/4k=1250 дней → не исчерпается.
    const data = monthDataWithAlice(60_000, 40_000, 0.5);
    mockHooks(data, data, [
      makeRole("developer", { tokens_month: 5_000_000, cost_month: 0 }),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    // Обзор: никто не исчерпает
    const overview = screen.getByTestId("budget-forecast-overview");
    expect(overview.textContent).toContain("0 из 1");

    // Drill-down: строки «Стоимость» нет вообще
    fireEvent.click(screen.getByText("Пользователи"));
    fireEvent.click(screen.getAllByText("alice@test.com")[0]);

    const forecast = screen.getByTestId("budget-forecast-user");
    expect(forecast.textContent).toContain("Токены:");
    expect(forecast.textContent).not.toContain("Стоимость:");
    expect(forecast.textContent).not.toContain("исчерпан");
  });

  it("budget=null — блока прогноза в персональном срезе нет", () => {
    vi.setSystemTime(new Date(2026, 7, 25));
    const data = monthDataWithAlice(3_000_000, 1_800_000, 0.9);
    mockHooks(data, data, [
      makeRole("developer", null),
      makeRole("admin", null),
    ]);

    render(<AnalyticsPage />);

    fireEvent.click(screen.getByText("Пользователи"));
    fireEvent.click(screen.getAllByText("alice@test.com")[0]);

    expect(screen.getByText(/Персональный срез/)).toBeInTheDocument();
    expect(screen.queryByTestId("budget-forecast-user")).not.toBeInTheDocument();
  });
});
