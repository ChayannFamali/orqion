import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

vi.mock("../api/client", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ status: "ok" }),
}));

vi.mock("../api/auth", () => ({
  apiGetMe: vi.fn(),
  apiLogin: vi.fn(),
  apiLogout: vi.fn(),
}));

vi.mock("../api/conversations", () => ({
  apiListConversations: vi.fn().mockResolvedValue({ conversations: [], total: 0 }),
  apiGetConversation: vi.fn(),
  apiCreateConversation: vi.fn(),
  apiUpdateConversation: vi.fn(),
  apiDeleteConversation: vi.fn(),
  apiResetConversationContext: vi.fn(),
}));

vi.mock("../api/models", () => ({
  apiListAvailableModels: vi.fn().mockResolvedValue([]),
}));

vi.mock("../api/corpora", () => ({
  apiListAvailableCorpora: vi.fn().mockResolvedValue({ corpora: [] }),
}));

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
  completeChat: vi.fn(),
}));

import { apiGetMe } from "../api/auth";

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    vi.mocked(apiGetMe).mockReturnValue(new Promise(() => {}));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    expect(document.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("shows login page when auth fails", async () => {
    vi.mocked(apiGetMe).mockRejectedValue(new Error("401"));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("orqion")).toBeInTheDocument();
    });
  });

  it("shows app layout when auth succeeds", async () => {
    vi.mocked(apiGetMe).mockResolvedValue({
      id: "user-1",
      email: "test@orqion.local",
      is_active: true,
      capabilities: ["chat"],
      reasoning: "off",
      is_impersonating: false,
      must_change_password: false,
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("test@orqion.local")).toBeInTheDocument();
    });
    expect(screen.getByText("Чат")).toBeInTheDocument();
  });
});
