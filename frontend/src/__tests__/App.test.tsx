import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";

vi.mock("../api/client", () => ({
  fetchHealth: vi.fn().mockResolvedValue({ status: "ok" }),
}));

describe("App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state initially", () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("shows ok status after health check resolves", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/orqion: ok/i)).toBeInTheDocument();
    });
  });
});
