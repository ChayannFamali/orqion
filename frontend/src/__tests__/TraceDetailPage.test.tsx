import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TraceDetailPage } from "../pages/TraceDetailPage";
import { useTraceDetail } from "../hooks/useTraces";
import type { TraceDetailResponse, SpanResponse } from "../api/types";

vi.mock("../hooks/useTraces");

function makeSpan(overrides: Partial<SpanResponse> = {}): SpanResponse {
  return {
    id: "span-1",
    name: "step_search",
    started_at: "2026-08-13T10:00:00Z",
    duration_ms: 42,
    payload: { step: "step_search", degraded: false, errors: [] },
    ...overrides,
  };
}

function mockTraceDetail(spans: SpanResponse[]): TraceDetailResponse {
  return {
    id: "trace-1",
    conversation_id: null,
    message_id: null,
    ts: "2026-08-13T10:00:00Z",
    total_ms: 150,
    status: "ok",
    spans,
  };
}

describe("TraceDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders span names and durations", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({ id: "s1", name: "prepare", duration_ms: 10 }),
        makeSpan({ id: "s2", name: "step_search", duration_ms: 30 }),
        makeSpan({ id: "s3", name: "step_rerank", duration_ms: 20 }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText("prepare")).toBeInTheDocument();
    expect(screen.getByText("step_search")).toBeInTheDocument();
    expect(screen.getByText("step_rerank")).toBeInTheDocument();
    expect(screen.getByText("10 ms")).toBeInTheDocument();
    expect(screen.getByText("30 ms")).toBeInTheDocument();
  });

  it("renders routing span with model and reason", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({
          id: "s1",
          name: "routing",
          payload: {
            rule_index: 0,
            reason: "default",
            model: "local/test-model",
            fallbacks: ["external/fallback"],
          },
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText("routing")).toBeInTheDocument();
    expect(screen.getByText("local/test-model")).toBeInTheDocument();
    expect(screen.getByText(/default/)).toBeInTheDocument();
    expect(screen.getByText("external/fallback")).toBeInTheDocument();
  });

  it("renders step_search with candidates count", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({
          id: "s1",
          name: "step_search",
          payload: {
            step: "step_search",
            degraded: false,
            errors: [],
            candidates_count: 50,
            candidates: [
              { chunk_id: "c1", score: 0.95, dense_rank: 1, sparse_rank: null },
            ],
          },
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText("Кандидатов: 50")).toBeInTheDocument();
  });

  it("renders step_rerank with reranked count", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({
          id: "s1",
          name: "step_rerank",
          payload: {
            step: "step_rerank",
            degraded: false,
            errors: [],
            reranked_count: 8,
            reranked: [{ chunk_id: "c1", score: 0.9, original_rank: 3 }],
          },
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText("Реранжировано: 8")).toBeInTheDocument();
  });

  it("renders degradation warning when degraded", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({
          id: "s1",
          name: "step_rewrite",
          payload: {
            step: "step_rewrite",
            degraded: true,
            errors: ["rewrite: timeout"],
          },
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText(/Деградация/)).toBeInTheDocument();
    expect(screen.getByText(/rewrite: timeout/)).toBeInTheDocument();
  });

  it("renders 'нет данных' for empty payload", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([
        makeSpan({
          id: "s1",
          name: "prepare",
          payload: {},
        }),
      ]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={vi.fn()} />);

    expect(screen.getByText(/нет данных/)).toBeInTheDocument();
  });

  it("calls onBack when back button clicked", () => {
    const onBack = vi.fn();
    vi.mocked(useTraceDetail).mockReturnValue({
      data: mockTraceDetail([makeSpan()]),
      isLoading: false,
      error: null,
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="trace-1" onBack={onBack} />);

    fireEvent.click(screen.getByText("Назад"));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it("shows not found for error state", () => {
    vi.mocked(useTraceDetail).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("404"),
    } as ReturnType<typeof useTraceDetail>);

    render(<TraceDetailPage traceId="nonexistent" onBack={vi.fn()} />);

    expect(screen.getByText("Трассировка не найдена")).toBeInTheDocument();
  });
});
