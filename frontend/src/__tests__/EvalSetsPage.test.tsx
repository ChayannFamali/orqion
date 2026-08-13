import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { EvalSetsPage } from "../pages/EvalSetsPage";
import {
  useEvalSets,
  useEvalSet,
  useCreateEvalSet,
  useDeleteEvalSet,
  useCreateEvalItem,
  useDeleteEvalItem,
} from "../hooks/useEval";
import type { CorpusResponse, EvalSetListResponse } from "../api/types";

vi.mock("../hooks/useEval");

vi.mock("../api/eval", () => ({
  apiListEvalSets: vi.fn(),
  apiCreateEvalSet: vi.fn(),
  apiGetEvalSet: vi.fn(),
  apiDeleteEvalSet: vi.fn(),
  apiCreateEvalItem: vi.fn(),
  apiDeleteEvalItem: vi.fn(),
  apiListEvalRuns: vi.fn(),
  apiCreateEvalRun: vi.fn(),
  apiCompareEvalRuns: vi.fn(),
}));

function makeCorpus(): CorpusResponse {
  return {
    id: "c1",
    name: "public",
    data_class: "К0",
    pinned_model_id: null,
    active_index_version_id: null,
  };
}

function mockListHooks(
  data?: EvalSetListResponse,
  error: unknown = null,
) {
  vi.mocked(useEvalSets).mockReturnValue({
    data,
    isLoading: false,
    error,
  } as ReturnType<typeof useEvalSets>);
  vi.mocked(useEvalSet).mockReturnValue({
    data: null,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useEvalSet>);
  vi.mocked(useCreateEvalSet).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCreateEvalSet>);
  vi.mocked(useDeleteEvalSet).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useDeleteEvalSet>);
  vi.mocked(useCreateEvalItem).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCreateEvalItem>);
  vi.mocked(useDeleteEvalItem).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useDeleteEvalItem>);
}

describe("EvalSetsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders eval set list", () => {
    mockListHooks({
      items: [
        { id: "e1", workspace_id: "w1", corpus_id: "c1", name: "Basic", created_at: "2026-01-01T00:00:00Z" },
        { id: "e2", workspace_id: "w1", corpus_id: "c1", name: "Advanced", created_at: "2026-01-02T00:00:00Z" },
      ],
    });

    render(<EvalSetsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Basic")).toBeInTheDocument();
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });

  it("shows empty state", () => {
    mockListHooks({ items: [] });

    render(<EvalSetsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Нет наборов оценки")).toBeInTheDocument();
  });

  it("shows create button", () => {
    mockListHooks({ items: [] });

    render(<EvalSetsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Создать набор")).toBeInTheDocument();
  });

  it("shows error state", () => {
    mockListHooks(undefined, new Error("fail"));

    render(<EvalSetsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Ошибка загрузки наборов оценки")).toBeInTheDocument();
  });
});
