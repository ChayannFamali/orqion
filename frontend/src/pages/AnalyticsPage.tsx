import { useState, useMemo } from "react";
import { Loader2, TrendingDown, TrendingUp, Users, BarChart3, Download } from "lucide-react";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { useAnalytics } from "../hooks/useAnalytics";
import { useRoles } from "../hooks/useRoles";
import {
  MIN_FORECAST_DAYS,
  forecastDimension,
  forecastText,
  monthProgress,
  type MonthProgress,
} from "../utils/budgetForecast";
import type {
  AnalyticsResponse,
  UserBreakdown,
  RoleResponse,
} from "../api/types";

const CHART_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
];

const BUDGET_WARNING_THRESHOLD = 0.8;

const NIL_ID = "00000000-0000-0000-0000-000000000000";

function userLabel(u: { user_id: string | null; user_email: string | null }): string {
  if (u.user_id === NIL_ID) return "Без пользователя";
  return u.user_email ?? "—";
}

function modelLabel(m: { model_id: string | null; model_alias: string | null }): string {
  if (m.model_id === NIL_ID) return "Без модели";
  return m.model_alias ?? m.model_id?.slice(0, 8) ?? "—";
}

type Tab = "overview" | "users";

export function AnalyticsPage() {
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [selectedUser, setSelectedUser] = useState<string | null>(null);

  const now = new Date();
  const sevenDaysAgo = new Date(now);
  sevenDaysAgo.setDate(now.getDate() - 7);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);

  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1);
  const today = new Date(now);
  const month = monthProgress(now);

  const fmt = (d: Date) => d.toISOString().slice(0, 10);

  const { data: weekData, isLoading: weekLoading, error: weekError } = useAnalytics({
    start: fmt(sevenDaysAgo),
    end: fmt(yesterday),
    model_limit: 8,
    model_sort: "requests",
    user_limit: 10,
    user_sort: "cost",
  });

  const { data: monthData, isLoading: monthLoading, error: monthError } = useAnalytics({
    start: fmt(monthStart),
    end: fmt(today),
    model_limit: 8,
    model_sort: "requests",
    user_limit: 10,
    user_sort: "cost",
  });

  const { data: rolesData } = useRoles();

  if (weekLoading || monthLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (weekError || monthError) {
    return (
      <div className="flex h-full items-center justify-center text-destructive">
        Ошибка загрузки аналитики
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-lg font-semibold">Аналитика потребления</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              const params = new URLSearchParams({
                start: fmt(sevenDaysAgo),
                end: fmt(yesterday),
                model_sort: "requests",
                user_sort: "cost",
              });
              window.open(`/api/analytics/export?${params}`, "_blank");
            }}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-sm text-muted-foreground transition-colors hover:bg-accent"
            title="Экспорт аналитики в CSV"
          >
            <Download className="h-4 w-4" />
            Экспорт CSV
          </button>
          <div className="flex gap-1 rounded-md border border-border p-0.5">
          <TabButton
            active={activeTab === "overview"}
            onClick={() => {
              setActiveTab("overview");
              setSelectedUser(null);
            }}
            icon={<BarChart3 className="h-4 w-4" />}
            label="Обзор"
          />
          <TabButton
            active={activeTab === "users"}
            onClick={() => setActiveTab("users")}
            icon={<Users className="h-4 w-4" />}
            label="Пользователи"
          />
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === "overview" ? (
          <OverviewTab
            weekData={weekData}
            monthData={monthData}
            roles={rolesData?.roles ?? []}
            month={month}
          />
        ) : (
          <UsersTab
            weekData={weekData}
            monthData={monthData}
            roles={rolesData?.roles ?? []}
            month={month}
            selectedUser={selectedUser}
            onSelectUser={setSelectedUser}
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "flex items-center gap-1 rounded px-3 py-1 text-sm transition-colors " +
        (active
          ? "bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-accent")
      }
    >
      {icon}
      {label}
    </button>
  );
}

/* ─── Overview Tab ─── */

function OverviewTab({
  weekData,
  monthData,
  roles,
  month,
}: {
  weekData?: AnalyticsResponse;
  monthData?: AnalyticsResponse;
  roles: RoleResponse[];
  month: MonthProgress;
}) {
  const summary = weekData?.summary;
  const byDay = weekData?.by_day ?? [];
  const byModel = weekData?.by_model ?? [];

  const roleBreakdown = useMemo(() => {
    return aggregateByRole(weekData?.by_user ?? []);
  }, [weekData]);

  const budgetStatus = useMemo(() => {
    return computeBudgetStatus(monthData?.by_user ?? [], roles, month);
  }, [monthData, roles, month]);

  const modelChartData = byModel.map((m) => ({
    name: modelLabel(m),
    requests: m.requests,
    tokens: m.tokens_in + m.tokens_out,
    cost: m.cost,
  }));

  const rolePieData = roleBreakdown.map((r) => ({
    name: r.role_name ?? "—",
    value: r.cost,
  }));

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard
          label="Запросы (7 дней)"
          value={summary ? formatNumber(summary.total_requests) : "—"}
        />
        <SummaryCard
          label="Токены (7 дней)"
          value={
            summary
              ? formatNumber(summary.total_tokens_in + summary.total_tokens_out)
              : "—"
          }
        />
        <SummaryCard
          label="Расход (7 дней)"
          value={summary ? `$${summary.total_cost.toFixed(2)}` : "—"}
        />
        <SummaryCard
          label="Ошибки (7 дней)"
          value={summary ? formatNumber(summary.total_errors) : "—"}
        />
      </div>

      {/* Daily requests chart */}
      <ChartCard title="Расход по дням (запросы и токены)">
        {byDay.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={byDay.map((d) => ({
              date: d.date,
              requests: d.requests,
              tokens: d.tokens_in + d.tokens_out,
            }))}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11 }}
                className="text-muted-foreground"
              />
              <YAxis tick={{ fontSize: 11 }} className="text-muted-foreground" />
              <Tooltip
                contentStyle={{
                  backgroundColor: "hsl(var(--background))",
                  border: "1px solid hsl(var(--border))",
                  borderRadius: "6px",
                  fontSize: "12px",
                }}
              />
              <Bar dataKey="requests" fill="#3b82f6" name="Запросы" />
              <Bar dataKey="tokens" fill="#10b981" name="Токены" />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <EmptyChart className="h-[280px]" />
        )}
      </ChartCard>

      {/* Two-column: Models + Roles */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="Разбивка по моделям (запросы)">
          {modelChartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={modelChartData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={100}
                  tick={{ fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "hsl(var(--background))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: "6px",
                    fontSize: "12px",
                  }}
                />
                <Bar dataKey="requests" fill="#3b82f6" name="Запросы" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyChart className="h-[250px]" />
          )}
        </ChartCard>

        <ChartCard title="Разбивка по ролям (расход)">
          {rolePieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={rolePieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={(entry) =>
                    `${entry.name}: $${Number(entry.value).toFixed(2)}`
                  }
                  labelLine={false}
                >
                  {rolePieData.map((_, i) => (
                    <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: "hsl(var(--background))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: "6px",
                    fontSize: "12px",
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <EmptyChart />
          )}
        </ChartCard>
      </div>

      {/* Budget burn status — aggregated only, no per-user names */}
      <ChartCard title="Сгорание бюджета (текущий месяц)">
        {budgetStatus.totalUsersWithBudget > 0 ? (
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div className="h-3 w-3 rounded-full bg-red-500" />
              <span className="text-sm">
                {budgetStatus.usersNearLimit} из {budgetStatus.totalUsersWithBudget}{" "}
                пользователей близки к лимиту (&gt;{Math.round(BUDGET_WARNING_THRESHOLD * 100)}%)
              </span>
            </div>
            <div className="flex items-center gap-2">
              <div className="h-3 w-3 rounded-full bg-muted" />
              <span className="text-sm text-muted-foreground">
                {budgetStatus.totalUsersWithoutBudget} без лимита
              </span>
            </div>
            {/* T-441 (Б2): агрегированный прогноз — без имён. При < 3 дней
                месяца прогноз не показывается вовсе (деградация А1). */}
            {budgetStatus.forecastAvailable && (
              <div className="flex items-center gap-2" data-testid="budget-forecast-overview">
                <div className="h-3 w-3 rounded-full bg-amber-500" />
                <span className="text-sm">
                  по прогнозу исчерпают к концу месяца:{" "}
                  {budgetStatus.usersProjectedToExhaust} из{" "}
                  {budgetStatus.totalUsersWithBudget}
                </span>
              </div>
            )}
          </div>
        ) : (
          <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
            Нет пользователей с установленным бюджетом
          </div>
        )}
      </ChartCard>
    </div>
  );
}

/* ─── Users Tab ─── */

function UsersTab({
  weekData,
  monthData,
  roles,
  month,
  selectedUser,
  onSelectUser,
}: {
  weekData?: AnalyticsResponse;
  monthData?: AnalyticsResponse;
  roles: RoleResponse[];
  month: MonthProgress;
  selectedUser: string | null;
  onSelectUser: (id: string | null) => void;
}) {
  const byUser = weekData?.by_user ?? [];
  const topUsers = useMemo(() => {
    return byUser;
  }, [byUser]);

  const budgetStatus = useMemo(() => {
    return computeBudgetStatus(monthData?.by_user ?? [], roles, month);
  }, [monthData, roles, month]);

  const selectedUserData = selectedUser
    ? monthData?.by_user?.find((u) => u.user_id === selectedUser)
    : null;

  const selectedBudget = selectedUser
    ? findBudgetForUser(selectedUser, byUser, roles)
    : null;

  // T-441 (Б2, В3): прогноз по выбранному пользователю, измерения
  // независимо; вырожденные лимиты (<= 0) → прогноза по измерению нет.
  const selectedTokensForecast =
    selectedUserData && selectedBudget
      ? forecastDimension(
          selectedUserData.tokens_in + selectedUserData.tokens_out,
          selectedBudget.tokensLimit > 0 ? selectedBudget.tokensLimit : null,
          month.daysElapsed,
          month.daysInMonth,
        )
      : null;
  const selectedCostForecast =
    selectedUserData && selectedBudget
      ? forecastDimension(
          selectedUserData.cost,
          selectedBudget.costLimit,
          month.daysElapsed,
          month.daysInMonth,
        )
      : null;

  const dailyDataForSelected = useMemo(() => {
    if (!selectedUser) return [];
    return (weekData?.by_day ?? []).map((d) => ({
      date: d.date,
      cost: d.cost,
    }));
  }, [selectedUser, weekData]);

  return (
    <div className="space-y-6">
      <ChartCard title="Верхние потребители (по расходу за 7 дней)">
        {topUsers.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-2 py-1 text-left">Пользователь</th>
                  <th className="px-2 py-1 text-left">Роль</th>
                  <th className="px-2 py-1 text-left">Команда</th>
                  <th className="px-2 py-1 text-right">Запросы</th>
                  <th className="px-2 py-1 text-right">Токены</th>
                  <th className="px-2 py-1 text-right">Расход</th>
                  <th className="px-2 py-1 text-right">Ошибки</th>
                </tr>
              </thead>
              <tbody>
                {topUsers.map((u) => (
                  <tr
                    key={u.user_id}
                    className={
                      "cursor-pointer border-b border-border transition-colors " +
                      (selectedUser === u.user_id
                        ? "bg-primary/10"
                        : "hover:bg-accent")
                    }
                    onClick={() =>
                      onSelectUser(selectedUser === u.user_id ? null : u.user_id ?? null)
                    }
                  >
                    <td className="px-2 py-1">{userLabel(u)}</td>
                    <td className="px-2 py-1 text-muted-foreground">{u.role_name ?? "—"}</td>
                    <td className="px-2 py-1 text-muted-foreground">{u.team_name ?? "—"}</td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatNumber(u.requests)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatNumber(u.tokens_in + u.tokens_out)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      ${u.cost.toFixed(2)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">{u.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyChart />
        )}
      </ChartCard>

      {/* Per-user budget status — only on Users tab, not Overview */}
      {budgetStatus.users.length > 0 && (
        <ChartCard title="Статус бюджета по пользователям (текущий месяц)">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="px-2 py-1 text-left">Пользователь</th>
                  <th className="px-2 py-1 text-left">Роль</th>
                  <th className="px-2 py-1 text-right">Лимит токенов</th>
                  <th className="px-2 py-1 text-right">Использовано</th>
                  <th className="px-2 py-1 text-right">% сгорания</th>
                  <th className="px-2 py-1 text-center">Статус</th>
                </tr>
              </thead>
              <tbody>
                {budgetStatus.users.map((u) => (
                  <tr key={u.userId} className="border-b border-border">
                    <td className="px-2 py-1">
                      {userLabel({ user_id: u.userId, user_email: u.email })}
                    </td>
                    <td className="px-2 py-1 text-muted-foreground">{u.roleName ?? "—"}</td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatNumber(u.tokensLimit)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {formatNumber(u.tokensUsed)}
                    </td>
                    <td className="px-2 py-1 text-right font-mono">
                      {Math.round(u.burnPercent * 100)}%
                    </td>
                    <td className="px-2 py-1 text-center">
                      {u.burnPercent >= BUDGET_WARNING_THRESHOLD ? (
                        <span className="inline-flex items-center gap-1 text-red-500">
                          <TrendingUp className="h-3 w-3" />
                          Высокий
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-green-500">
                          <TrendingDown className="h-3 w-3" />
                          Норма
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ChartCard>
      )}

      {selectedUser && selectedUserData && (
        <ChartCard
          title={`Персональный срез: ${userLabel(selectedUserData)}`}
        >
          <div className="space-y-4">
            {/* Personal summary */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <SummaryCard
                label="Запросы (месяц)"
                value={formatNumber(selectedUserData.requests)}
              />
              <SummaryCard
                label="Токены (месяц)"
                value={formatNumber(
                  selectedUserData.tokens_in + selectedUserData.tokens_out,
                )}
              />
              <SummaryCard
                label="Расход (месяц)"
                value={`$${selectedUserData.cost.toFixed(2)}`}
              />
              <SummaryCard
                label="Ошибки (месяц)"
                value={formatNumber(selectedUserData.errors)}
              />
            </div>

            {/* Budget burn-down chart */}
            {selectedBudget && (
              <div>
                <h4 className="mb-2 text-sm font-medium">
                  Сгорание бюджета ({selectedBudget.tokensLimit > 0 ? "токены" : "стоимость"})
                </h4>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart
                    data={[
                      {
                        date: "Лимит",
                        value:
                          selectedBudget.tokensLimit > 0
                            ? selectedBudget.tokensLimit
                            : selectedBudget.costLimit ?? 0,
                      },
                      {
                        date: "Факт",
                        value:
                          selectedBudget.tokensLimit > 0
                            ? selectedUserData.tokens_in + selectedUserData.tokens_out
                            : selectedUserData.cost,
                      },
                    ]}
                  >
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "hsl(var(--background))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontSize: "12px",
                      }}
                    />
                    <Line
                      dataKey="value"
                      stroke="#3b82f6"
                      strokeWidth={2}
                      name="Токены"
                    />
                  </LineChart>
                </ResponsiveContainer>
                {selectedBudget.tokensLimit > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Использовано:{" "}
                    {Math.round(
                      ((selectedUserData.tokens_in + selectedUserData.tokens_out) /
                        selectedBudget.tokensLimit) *
                        100,
                    )}
                    % от лимита {formatNumber(selectedBudget.tokensLimit)}
                  </p>
                )}
              </div>
            )}

            {/* T-441 (Б2): прогноз по выбранному пользователю. При < 3 дней
                месяца не показывается вовсе (деградация А1). */}
            {month.daysElapsed >= MIN_FORECAST_DAYS &&
              (selectedTokensForecast || selectedCostForecast) && (
                <div data-testid="budget-forecast-user">
                  <h4 className="mb-2 text-sm font-medium">
                    Прогноз бюджета (текущий месяц)
                  </h4>
                  <ul className="space-y-1 text-xs text-muted-foreground">
                    {selectedTokensForecast && (
                      <li>Токены: {forecastText(selectedTokensForecast)}</li>
                    )}
                    {selectedCostForecast && (
                      <li>Стоимость: {forecastText(selectedCostForecast)}</li>
                    )}
                  </ul>
                </div>
              )}

            {/* Daily cost chart */}
            <div>
              <h4 className="mb-2 text-sm font-medium">Расход по дням</h4>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={dailyDataForSelected}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "hsl(var(--background))",
                      border: "1px solid hsl(var(--border))",
                      borderRadius: "6px",
                      fontSize: "12px",
                    }}
                  />
                  <Bar dataKey="cost" fill="#f59e0b" name="Расход ($)" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </ChartCard>
      )}
    </div>
  );
}

/* ─── Helpers ─── */

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function ChartCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-border p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {children}
    </div>
  );
}

function EmptyChart({ className = "h-32" }: { className?: string }) {
  return (
    <div
      className={`flex items-center justify-center text-sm text-muted-foreground ${className}`}
    >
      Нет данных
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface RoleAggregate {
  role_name: string | null;
  requests: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  errors: number;
}

function aggregateByRole(byUser: UserBreakdown[]): RoleAggregate[] {
  const map = new Map<string, RoleAggregate>();
  for (const u of byUser) {
    const key = u.role_name ?? "—";
    const existing = map.get(key);
    if (existing) {
      existing.requests += u.requests;
      existing.tokens_in += u.tokens_in;
      existing.tokens_out += u.tokens_out;
      existing.cost += u.cost;
      existing.errors += u.errors;
    } else {
      map.set(key, {
        role_name: u.role_name,
        requests: u.requests,
        tokens_in: u.tokens_in,
        tokens_out: u.tokens_out,
        cost: u.cost,
        errors: u.errors,
      });
    }
  }
  return Array.from(map.values()).sort((a, b) => b.cost - a.cost);
}

interface BudgetUserStatus {
  userId: string;
  email: string | null;
  roleName: string | null;
  tokensLimit: number;
  costLimit: number | null;
  tokensUsed: number;
  burnPercent: number;
  /** T-441: прогноз исчерпания хотя бы по одному измерению. */
  projectedToExhaust: boolean;
}

interface BudgetStatus {
  users: BudgetUserStatus[];
  usersNearLimit: number;
  totalUsersWithBudget: number;
  totalUsersWithoutBudget: number;
  /** T-441: сколько пользователей по прогнозу исчерпают к концу месяца. */
  usersProjectedToExhaust: number;
  /** Достаточно ли данных для прогноза (>= 3 дней месяца, деградация А1). */
  forecastAvailable: boolean;
}

function computeBudgetStatus(
  byUser: UserBreakdown[],
  roles: RoleResponse[],
  month: MonthProgress,
): BudgetStatus {
  const roleBudgets = new Map<string, { tokens: number; cost: number | null }>();
  for (const role of roles) {
    const budget = role.policy.budget as Record<string, number> | null;
    if (budget) {
      roleBudgets.set(role.name, {
        tokens: budget.tokens_month ?? 0,
        cost: budget.cost_month ?? null,
      });
    } else {
      roleBudgets.set(role.name, { tokens: 0, cost: null });
    }
  }

  const forecastAvailable = month.daysElapsed >= MIN_FORECAST_DAYS;

  const users: BudgetUserStatus[] = [];
  let usersNearLimit = 0;
  let usersProjectedToExhaust = 0;
  let totalWithoutBudget = 0;

  for (const u of byUser) {
    if (!u.user_id || u.user_id === NIL_ID) continue;

    const roleName = u.role_name ?? "";
    const roleBudget = roleBudgets.get(roleName);

    if (!roleBudget || roleBudget.tokens === 0) {
      totalWithoutBudget++;
      continue;
    }

    const tokensUsed = u.tokens_in + u.tokens_out;
    const burnPercent = tokensUsed / roleBudget.tokens;
    const nearLimit = burnPercent >= BUDGET_WARNING_THRESHOLD;

    if (nearLimit) usersNearLimit++;

    // T-441 (В3): измерения независимо; вырожденный лимит (<= 0) →
    // прогноза по измерению нет (cost_month=0 ≠ «исчерпан»).
    const tokensForecast = forecastDimension(
      tokensUsed,
      roleBudget.tokens > 0 ? roleBudget.tokens : null,
      month.daysElapsed,
      month.daysInMonth,
    );
    const costForecast = forecastDimension(
      u.cost,
      roleBudget.cost,
      month.daysElapsed,
      month.daysInMonth,
    );
    const exhausts =
      (tokensForecast?.kind === "exhaustion" || costForecast?.kind === "exhaustion") ??
      false;
    if (exhausts) usersProjectedToExhaust++;

    users.push({
      userId: u.user_id,
      email: u.user_email,
      roleName: u.role_name,
      tokensLimit: roleBudget.tokens,
      costLimit: roleBudget.cost,
      tokensUsed,
      burnPercent,
      projectedToExhaust: exhausts,
    });
  }

  return {
    users: users.sort((a, b) => b.burnPercent - a.burnPercent),
    usersNearLimit,
    totalUsersWithBudget: users.length,
    totalUsersWithoutBudget: totalWithoutBudget,
    usersProjectedToExhaust,
    forecastAvailable,
  };
}

function findBudgetForUser(
  userId: string,
  byUser: UserBreakdown[],
  roles: RoleResponse[],
): { tokensLimit: number; costLimit: number | null } | null {
  if (userId === NIL_ID) return null;
  const userData = byUser.find((u) => u.user_id === userId);
  if (!userData) return null;

  const roleName = userData.role_name;
  if (!roleName) return null;

  const role = roles.find((r) => r.name === roleName);
  if (!role) return null;

  const budget = role.policy.budget as Record<string, number> | null;
  if (!budget) return null;

  return {
    tokensLimit: budget.tokens_month ?? 0,
    costLimit: budget.cost_month ?? null,
  };
}
