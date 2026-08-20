import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UsageWidget } from "../components/UsageWidget";
import type { MyUsageResponse } from "../api/me";

vi.mock("../hooks/useMyUsage", () => ({
  useMyUsage: vi.fn(),
}));

import { useMyUsage } from "../hooks/useMyUsage";

function mockUsageData(overrides: Partial<MyUsageResponse> = {}): MyUsageResponse {
  return {
    tokens_used: 1500,
    tokens_limit: 5000000,
    cost_used: 0.01,
    cost_limit: 10,
    period: "2026-08",
    by_model: [
      {
        model_id: "gpt-4",
        requests: 10,
        tokens_in: 1000,
        tokens_out: 500,
        cost: 0.01,
      },
    ],
    ...overrides,
  };
}

describe("UsageWidget", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders token usage with limit in collapsed state", () => {
    vi.mocked(useMyUsage).mockReturnValue({
      data: mockUsageData({ tokens_used: 1500 }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMyUsage>);

    render(<UsageWidget />);

    // formatTokens(1500) = (1500/1000).toFixed(0) = "2K"
    expect(screen.getByText("2K")).toBeInTheDocument();
    expect(screen.getByText(/5.0M/)).toBeInTheDocument();
  });

  it("shows 'без лимита' for unlimited tokens and cost (admin)", () => {
    vi.mocked(useMyUsage).mockReturnValue({
      data: mockUsageData({
        tokens_limit: null,
        cost_limit: null,
        tokens_used: 15000,
      }),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMyUsage>);

    render(<UsageWidget />);

    expect(screen.getByText("15K")).toBeInTheDocument();
    // Collapsed: no limit shown next to the used value
    expect(screen.queryByText(/5M/)).not.toBeInTheDocument();

    // Expand and check "без лимита" for both metrics
    fireEvent.click(screen.getByRole("button"));

    expect(screen.getAllByText(/без лимита/).length).toBe(2);
  });

  it("expands to show by-model breakdown on click", async () => {
    vi.mocked(useMyUsage).mockReturnValue({
      data: mockUsageData(),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useMyUsage>);

    render(<UsageWidget />);

    // Collapsed: model breakdown not visible
    expect(screen.queryByText("По моделям:")).not.toBeInTheDocument();

    // Click to expand
    fireEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(screen.getByText("По моделям:")).toBeInTheDocument();
      expect(screen.getByText("gpt-4")).toBeInTheDocument();
    });
  });

  it("renders nothing when loading or error", () => {
    vi.mocked(useMyUsage).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useMyUsage>);

    const { container } = render(<UsageWidget />);
    expect(container.firstChild).toBeNull();
  });
});
