import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { UsersPage } from "../pages/UsersPage";
import { useUsers, useUpdateUser, useImpersonateUser } from "../hooks/useUsers";
import { useRoles } from "../hooks/useRoles";
import type { UserListResponse } from "../api/types";

vi.mock("../hooks/useUsers");
vi.mock("../hooks/useRoles");

function makeUser(overrides: Partial<UserListResponse["users"][0]> = {}) {
  return {
    id: "u1",
    email: "dev@orqion.local",
    is_active: true,
    role_id: "r1",
    role_name: "developer",
    is_builtin_role: true,
    ...overrides,
  };
}

function mockUseRoles() {
  vi.mocked(useRoles).mockReturnValue({
    data: {
      roles: [
        { id: "r1", name: "developer", is_builtin: true, policy: {} },
        { id: "r2", name: "support", is_builtin: true, policy: {} },
        { id: "r3", name: "admin", is_builtin: true, policy: { capabilities: ["*"] } },
      ],
    },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useRoles>);
}

function mockHooks(
  usersData?: UserListResponse,
  error: unknown = null,
) {
  vi.mocked(useUsers).mockReturnValue({
    data: usersData,
    isLoading: false,
    error,
  } as ReturnType<typeof useUsers>);
  vi.mocked(useUpdateUser).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useUpdateUser>);
  vi.mocked(useImpersonateUser).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useImpersonateUser>);
  mockUseRoles();
}

describe("UsersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders user list with email and role", () => {
    mockHooks({
      users: [
        makeUser({ id: "u1", email: "dev@orqion.local", role_name: "developer" }),
        makeUser({ id: "u2", email: "admin@orqion.local", role_name: "admin" }),
      ],
    });

    render(<UsersPage />);

    expect(screen.getByText("dev@orqion.local")).toBeInTheDocument();
    expect(screen.getByText("admin@orqion.local")).toBeInTheDocument();
    expect(screen.getAllByText("активен")).toHaveLength(2);
  });

  it("shows empty state when no users", () => {
    mockHooks({ users: [] });

    render(<UsersPage />);

    expect(screen.getByText("Нет пользователей")).toBeInTheDocument();
  });

  it("shows error state on failure", () => {
    mockHooks(undefined, new Error("fetch failed"));

    render(<UsersPage />);

    expect(screen.getByText("Ошибка загрузки пользователей")).toBeInTheDocument();
  });

  it("shows loading spinner", () => {
    vi.mocked(useUsers).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useUsers>);
    vi.mocked(useUpdateUser).mockReturnValue({} as ReturnType<typeof useUpdateUser>);
    vi.mocked(useImpersonateUser).mockReturnValue({} as ReturnType<typeof useImpersonateUser>);
    mockUseRoles();

    const { container } = render(<UsersPage />);
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("opens edit modal with role select and status toggle", () => {
    mockHooks({
      users: [makeUser({ id: "u1", email: "dev@orqion.local" })],
    });

    render(<UsersPage />);

    fireEvent.click(screen.getByText("Изменить"));

    // В модалке заголовок h3 содержит email
    expect(screen.getByRole("heading", { name: "dev@orqion.local" })).toBeInTheDocument();
    expect(screen.getByText("Сохранить")).toBeInTheDocument();
    expect(screen.getByText("Войти от имени")).toBeInTheDocument();
  });

  it("shows inactive badge for deactivated user", () => {
    mockHooks({
      users: [makeUser({ id: "u1", is_active: false })],
    });

    render(<UsersPage />);

    expect(screen.getByText("отключён")).toBeInTheDocument();
  });

  it("shows impersonate confirm dialog on first click", () => {
    mockHooks({
      users: [makeUser({ id: "u1", email: "dev@orqion.local" })],
    });

    render(<UsersPage />);

    fireEvent.click(screen.getByText("Изменить"));
    fireEvent.click(screen.getByText("Войти от имени"));

    expect(screen.getByText(/Войти от имени dev@orqion.local/)).toBeInTheDocument();
    expect(screen.getByText("Подтвердить вход")).toBeInTheDocument();
  });
});
