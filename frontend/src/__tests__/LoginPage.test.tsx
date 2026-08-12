import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LoginPage } from "../pages/LoginPage";

vi.mock("../api/auth", () => ({
  apiGetMe: vi.fn(),
  apiLogin: vi.fn(),
  apiLogout: vi.fn(),
}));

import { apiLogin } from "../api/auth";
import type { ApiError } from "../api/types";

function renderLoginPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <LoginPage />
    </QueryClientProvider>,
  );
  return client;
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders email and password fields", () => {
    renderLoginPage();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toBeInTheDocument();
    expect(screen.getByText("Войти")).toBeInTheDocument();
  });

  it("submits login form with email and password", async () => {
    vi.mocked(apiLogin).mockResolvedValue({
      user: { id: "1", email: "test@orqion.local", is_active: true, capabilities: ["chat"] },
    });

    renderLoginPage();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "test@orqion.local");
    await user.type(screen.getByLabelText("Пароль"), "password123");
    await user.click(screen.getByText("Войти"));

    await waitFor(() => {
      expect(apiLogin).toHaveBeenCalledWith({
        email: "test@orqion.local",
        password: "password123",
      });
    });
  });

  it("shows error message on login failure", async () => {
    const error: ApiError = {
      error: "invalid_credentials",
      reason: "Неверный email или пароль",
      constraint: null,
      hint: null,
    };
    vi.mocked(apiLogin).mockRejectedValue(error);

    renderLoginPage();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "bad@orqion.local");
    await user.type(screen.getByLabelText("Пароль"), "wrong");
    await user.click(screen.getByText("Войти"));

    await waitFor(() => {
      expect(screen.getByText("Неверный email или пароль")).toBeInTheDocument();
    });
  });

  it("disables submit button when fields are empty", () => {
    renderLoginPage();
    expect(screen.getByText("Войти")).toBeDisabled();
  });

  it("shows loading state with spinner during submission", async () => {
    vi.mocked(apiLogin).mockReturnValue(new Promise(() => {}));

    renderLoginPage();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "test@orqion.local");
    await user.type(screen.getByLabelText("Пароль"), "password123");
    await user.click(screen.getByText("Войти"));

    await waitFor(() => {
      expect(screen.getByText("Вход…")).toBeInTheDocument();
    });
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("shows hint for rate-limited login", async () => {
    const error: ApiError = {
      error: "login_rate_limited",
      reason: "Слишком много попыток входа",
      constraint: { max_attempts: 5, period_seconds: 60, reset_in_seconds: 45.0 },
      hint: "Попробуйте через 45 секунд",
    };
    vi.mocked(apiLogin).mockRejectedValue(error);

    renderLoginPage();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "test@orqion.local");
    await user.type(screen.getByLabelText("Пароль"), "password123");
    await user.click(screen.getByText("Войти"));

    await waitFor(() => {
      expect(screen.getByText("Слишком много попыток входа")).toBeInTheDocument();
    });
    expect(screen.getByText("Попробуйте через 45 секунд")).toBeInTheDocument();
  });

  it("clears error and transitions after successful login", async () => {
    vi.mocked(apiLogin).mockResolvedValue({
      user: { id: "1", email: "test@orqion.local", is_active: true, capabilities: ["chat"] },
    });

    const client = renderLoginPage();

    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "test@orqion.local");
    await user.type(screen.getByLabelText("Пароль"), "password123");
    await user.click(screen.getByText("Войти"));

    await waitFor(() => {
      expect(apiLogin).toHaveBeenCalledWith({
        email: "test@orqion.local",
        password: "password123",
      });
    });

    // After success, mutation is resolved — no error displayed
    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    // QueryClient should have been invalidated (auth.me key)
    expect(client.getQueryCache().findAll()).toHaveLength(0);
  });
});
