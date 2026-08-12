import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TraceListPage } from "../pages/TraceListPage";
import { useTraces } from "../hooks/useTraces";
import type { TraceListResponse } from "../api/types";

vi.mock("../hooks/useTraces");

function mockTracesResponse(traces: TraceListResponse["traces"], total: number = traces.length): TraceListResponse {
  return { traces, total };
}

function makeTrace(overrides: Partial<TraceListResponse["traces"][0]> = {}) {
  return {
    id: "trace-1",
    conversation_id: null,
    message_id: null,
    ts: "2026-08-13T10:00:00Z",
    total_ms: 150,
    status: "ok",
    span_count: 3,
    ...overrides,
  };
}

describe("TraceListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders trace list with timestamp and span count", () => {
    vi.mocked(useTraces).mockReturnValue({
      data: mockTracesResponse([
        makeTrace({ id: "t1", total_ms: 200, span_count: 5 }),
        makeTrace({ id: "t2", total_ms: 50, span_count: 2, status: "error" }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraces>);

    render(<TraceListPage onTraceSelect={vi.fn()} />);

    expect(screen.getByText(/Всего: 2/)).toBeInTheDocument();
    expect(screen.getByText("200 ms")).toBeInTheDocument();
    expect(screen.getByText("50 ms")).toBeInTheDocument();
    expect(screen.getByText("5 шагов")).toBeInTheDocument();
    expect(screen.getByText("2 шагов")).toBeInTheDocument();
  });

  it("calls onTraceSelect when trace is clicked", () => {
    const onTraceSelect = vi.fn();
    vi.mocked(useTraces).mockReturnValue({
      data: mockTracesResponse([makeTrace({ id: "trace-click-test" })]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraces>);

    render(<TraceListPage onTraceSelect={onTraceSelect} />);

    const button = screen.getByRole("button");
    fireEvent.click(button);
    expect(onTraceSelect).toHaveBeenCalledWith("trace-click-test");
  });

  it("shows empty state when no traces", () => {
    vi.mocked(useTraces).mockReturnValue({
      data: mockTracesResponse([]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraces>);

    render(<TraceListPage onTraceSelect={vi.fn()} />);

    expect(screen.getByText("Нет трассировок")).toBeInTheDocument();
  });

  it("shows error state on error", () => {
    vi.mocked(useTraces).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("fetch failed"),
    } as ReturnType<typeof useTraces>);

    render(<TraceListPage onTraceSelect={vi.fn()} />);

    expect(screen.getByText("Ошибка загрузки трассировок")).toBeInTheDocument();
  });

  it("shows loading spinner", () => {
    vi.mocked(useTraces).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useTraces>);

    const { container } = render(<TraceListPage onTraceSelect={vi.fn()} />);

    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });
});
