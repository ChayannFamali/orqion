import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AuditLogPage } from "../pages/AuditLogPage";
import { useAuditLog, useAuditActions } from "../hooks/useAudit";
import type { AuditLogListResponse, AuditActionsResponse } from "../api/types";

vi.mock("../hooks/useAudit");
vi.mock("../api/audit", () => ({
  apiListAuditLog: vi.fn(),
  apiGetAuditActions: vi.fn(),
}));

function makeEntry(
  overrides: Partial<{
    id: string;
    action: string;
    object_type: string;
    object_id: string | null;
    actor_user_id: string;
    meta: Record<string, unknown>;
    ts: string;
  }> = {},
) {
  return {
    id: "entry-1",
    ts: "2026-08-13T10:00:00Z",
    actor_user_id: "user-123-456",
    action: "role.policy_changed",
    object_type: "role",
    object_id: "role-abc",
    meta: { old: { max_input_tokens: 16000 }, new: { max_input_tokens: 32000 } },
    ...overrides,
  };
}

function mockHooks(
  entries?: ReturnType<typeof makeEntry>[],
  total?: number,
  actions?: string[],
  loading = false,
  error: unknown = null,
) {
  vi.mocked(useAuditLog).mockReturnValue({
    data: entries
      ? ({
          entries,
          total: total ?? entries.length,
        } as AuditLogListResponse)
      : undefined,
    isLoading: loading,
    error,
  } as ReturnType<typeof useAuditLog>);

  vi.mocked(useAuditActions).mockReturnValue({
    data: actions
      ? ({ actions } as AuditActionsResponse)
      : { actions: [] } as AuditActionsResponse,
    isLoading: false,
    error: null,
  } as ReturnType<typeof useAuditActions>);
}

describe("AuditLogPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state", () => {
    mockHooks(undefined, undefined, undefined, true);

    render(<AuditLogPage />);

    expect(document.querySelector(".animate-spin")).toBeTruthy();
  });

  it("renders error state", () => {
    mockHooks(undefined, undefined, undefined, false, new Error("fail"));

    render(<AuditLogPage />);

    expect(screen.getByText("Ошибка загрузки журнала аудита")).toBeInTheDocument();
  });

  it("renders empty state", () => {
    mockHooks([], 0, []);

    render(<AuditLogPage />);

    expect(screen.getByText("Нет записей")).toBeInTheDocument();
    expect(screen.getByText("Всего записей: 0")).toBeInTheDocument();
  });

  it("renders audit entries table with action and object", () => {
    const entry = makeEntry();
    mockHooks([entry], 1, ["role.policy_changed"]);

    render(<AuditLogPage />);

    expect(screen.getByText("Всего записей: 1")).toBeInTheDocument();
    // "role.policy_changed" appears in both <option> and <td>
    expect(screen.getAllByText("role.policy_changed").length).toBeGreaterThanOrEqual(1);
  });

  it("renders action filter dropdown with actions from backend", () => {
    const entry = makeEntry();
    mockHooks([entry], 1, [
      "role.created",
      "role.policy_changed",
      "impersonate",
      "impersonate.exit",
    ]);

    render(<AuditLogPage />);

    // Check the select has the actions
    const select = screen.getByDisplayValue("Все действия");
    expect(select).toBeInTheDocument();
    const options = select.querySelectorAll("option");
    expect(options.length).toBe(5); // "Все действия" + 4 actions
    expect(select.querySelector('option[value="impersonate.exit"]')).toBeTruthy();
  });

  it("renders pagination when total > page size", () => {
    const entries = Array.from({ length: 50 }, (_, i) =>
      makeEntry({ id: `entry-${i}`, action: "test.action" }),
    );
    mockHooks(entries, 120, ["test.action"]);

    render(<AuditLogPage />);

    expect(screen.getByText(/из 120/)).toBeInTheDocument();
    expect(screen.getByText("Назад")).toBeInTheDocument();
    expect(screen.getByText("Вперёд")).toBeInTheDocument();
  });

  it("does not render pagination when total <= page size", () => {
    const entry = makeEntry();
    mockHooks([entry], 1, ["role.policy_changed"]);

    render(<AuditLogPage />);

    expect(screen.queryByText("Назад")).not.toBeInTheDocument();
    expect(screen.queryByText("Вперёд")).not.toBeInTheDocument();
  });

  it("expands row to show meta JSON on click", () => {
    const entry = makeEntry({
      meta: { old: { max_input_tokens: 16000 }, new: { max_input_tokens: 32000 } },
    });
    mockHooks([entry], 1, ["role.policy_changed"]);

    render(<AuditLogPage />);

    // Initially no <pre> with full JSON is visible
    expect(document.querySelector("pre")).not.toBeTruthy();

    // Click the row to expand — "role.policy_changed" appears in both the
    // filter <option> and the table <td>, so use getAllByText and click the <td>
    const actionElements = screen.getAllByText("role.policy_changed");
    const tdElement = actionElements.find((el) => el.tagName === "TD");
    expect(tdElement).toBeTruthy();
    fireEvent.click(tdElement!);

    // Full meta JSON should be visible in the expanded <pre> row
    const pre = document.querySelector("pre");
    expect(pre).toBeTruthy();
    expect(pre?.textContent).toContain('"max_input_tokens": 16000');
    expect(pre?.textContent).toContain('"max_input_tokens": 32000');
  });

  it("collapses expanded row on second click", () => {
    const entry = makeEntry();
    mockHooks([entry], 1, ["role.policy_changed"]);

    render(<AuditLogPage />);

    const actionElements = screen.getAllByText("role.policy_changed");
    const tdElement = actionElements.find((el) => el.tagName === "TD")!;

    // Expand
    fireEvent.click(tdElement);
    expect(document.querySelector("pre")).toBeTruthy();

    // Collapse
    fireEvent.click(tdElement);
    expect(document.querySelector("pre")).not.toBeTruthy();
  });

  it("does not render any edit or delete buttons", () => {
    const entry = makeEntry();
    mockHooks([entry], 1, ["role.policy_changed"]);

    render(<AuditLogPage />);

    // No edit/delete buttons — audit log is append-only
    expect(screen.queryByText(/удалить/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/изменить/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/редактировать/i)).not.toBeInTheDocument();
  });

  it("renders filter controls", () => {
    mockHooks([], 0, []);

    render(<AuditLogPage />);

    expect(screen.getByPlaceholderText("ID пользователя")).toBeInTheDocument();
    expect(screen.getByText("Применить")).toBeInTheDocument();
    expect(screen.getByText("Сбросить")).toBeInTheDocument();
  });
});
