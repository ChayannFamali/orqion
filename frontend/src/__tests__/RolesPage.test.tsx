import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RolesPage } from "../pages/RolesPage";
import { useRoles, useCreateRole, useUpdateRole } from "../hooks/useRoles";
import type { RoleListResponse } from "../api/types";

vi.mock("../hooks/useRoles");

function makeRole(overrides: Partial<RoleListResponse["roles"][0]> = {}) {
  return {
    id: "role-1",
    name: "support",
    is_builtin: true,
    policy: {
      models: ["local/*"],
      max_input_tokens: 16000,
      max_output_tokens: 2000,
      reasoning: "off",
      budget: { tokens_month: 2000000, cost_month: 0 },
      rpm: 30,
      tpm: 20000,
      corpora: ["public"],
      capabilities: ["chat"],
    },
    ...overrides,
  };
}

function mockRolesResponse(roles: RoleListResponse["roles"]): RoleListResponse {
  return { roles };
}

function mockHooks(
  rolesData?: RoleListResponse,
  error: unknown = null,
) {
  vi.mocked(useRoles).mockReturnValue({
    data: rolesData,
    isLoading: false,
    error,
  } as ReturnType<typeof useRoles>);
  vi.mocked(useCreateRole).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCreateRole>);
  vi.mocked(useUpdateRole).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useUpdateRole>);
}

describe("RolesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders role list with name and builtin badge", () => {
    mockHooks(
      mockRolesResponse([
        makeRole({ id: "r1", name: "support", is_builtin: true }),
        makeRole({ id: "r2", name: "admin", is_builtin: true, policy: { models: ["*"], capabilities: ["*"], corpora: ["*"], reasoning: "optional", max_input_tokens: null, max_output_tokens: null, budget: null, rpm: null, tpm: null } }),
      ]),
    );

    render(<RolesPage />);

    expect(screen.getByText("support")).toBeInTheDocument();
    expect(screen.getByText("admin")).toBeInTheDocument();
    expect(screen.getAllByText("встроенная")).toHaveLength(2);
  });

  it("shows admin role with shield icon and wildcard text", () => {
    mockHooks(
      mockRolesResponse([
        makeRole({
          id: "r1",
          name: "admin",
          is_builtin: true,
          policy: { models: ["*"], capabilities: ["*"], corpora: ["*"], reasoning: "optional", max_input_tokens: null, max_output_tokens: null, budget: null, rpm: null, tpm: null },
        }),
      ]),
    );

    render(<RolesPage />);

    expect(screen.getByText("Все права (admin)")).toBeInTheDocument();
  });

  it("shows empty state when no roles", () => {
    mockHooks(mockRolesResponse([]));

    render(<RolesPage />);

    expect(screen.getByText("Нет ролей")).toBeInTheDocument();
  });

  it("shows error state on failure", () => {
    mockHooks(undefined, new Error("fetch failed"));

    render(<RolesPage />);

    expect(screen.getByText("Ошибка загрузки ролей")).toBeInTheDocument();
  });

  it("shows loading spinner", () => {
    vi.mocked(useRoles).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useRoles>);
    vi.mocked(useCreateRole).mockReturnValue({} as ReturnType<typeof useCreateRole>);
    vi.mocked(useUpdateRole).mockReturnValue({} as ReturnType<typeof useUpdateRole>);

    const { container } = render(<RolesPage />);
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("opens create role modal on button click", () => {
    mockHooks(mockRolesResponse([makeRole()]));

    render(<RolesPage />);

    fireEvent.click(screen.getByText("Добавить"));

    expect(screen.getByText("Новая роль")).toBeInTheDocument();
    expect(screen.getByText("Форма")).toBeInTheDocument();
    expect(screen.getByText("JSON")).toBeInTheDocument();
  });

  it("opens edit role modal with policy fields", () => {
    mockHooks(
      mockRolesResponse([
        makeRole({ id: "r1", name: "developer", is_builtin: true }),
      ]),
    );

    render(<RolesPage />);

    fireEvent.click(screen.getByText("Изменить"));

    // В модалке заголовок h3 содержит имя роли
    expect(screen.getByRole("heading", { name: "developer" })).toBeInTheDocument();
    expect(screen.getByText("Сохранить")).toBeInTheDocument();
  });

  it("switches to JSON editor mode", () => {
    mockHooks(mockRolesResponse([makeRole()]));

    render(<RolesPage />);

    fireEvent.click(screen.getByText("Добавить"));
    fireEvent.click(screen.getByText("JSON"));

    expect(screen.getByPlaceholderText('{"models": ["local/*"], ...}')).toBeInTheDocument();
  });

  it("shows capabilities checkboxes in form mode", () => {
    mockHooks(mockRolesResponse([makeRole()]));

    render(<RolesPage />);

    fireEvent.click(screen.getByText("Добавить"));

    expect(screen.getAllByText("chat").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("upload").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("manage_corpora")).toBeInTheDocument();
    expect(screen.getByText("view_analytics")).toBeInTheDocument();
    expect(screen.getByText("view_traces")).toBeInTheDocument();
    expect(screen.getByText("manage_providers")).toBeInTheDocument();
  });

  it("shows lockout warning for admin role when removing wildcard", () => {
    const adminPolicy = {
      models: ["*"],
      capabilities: ["*"],
      corpora: ["*"],
      reasoning: "optional",
      max_input_tokens: null,
      max_output_tokens: null,
      budget: null,
      rpm: null,
      tpm: null,
    };
    mockHooks(
      mockRolesResponse([
        makeRole({ id: "r1", name: "admin", is_builtin: true, policy: adminPolicy }),
      ]),
    );

    render(<RolesPage />);

    fireEvent.click(screen.getByText("Изменить"));

    // Снимаем * с capabilities (admin wildcard) — должен появиться warning
    const wildcardCheckbox = screen.getAllByRole("checkbox")[0];
    expect((wildcardCheckbox as HTMLInputElement).checked).toBe(true);
    fireEvent.click(wildcardCheckbox);

    fireEvent.click(screen.getByText("Сохранить"));

    expect(screen.getByText(/лишить вас административного доступа/i)).toBeInTheDocument();
  });
});
