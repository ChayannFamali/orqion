import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EvalRunsPage } from "../pages/EvalRunsPage";
import {
  useEvalRuns,
  useCreateEvalRun,
  useCompareEvalRuns,
} from "../hooks/useEval";
import { useIndexVersions } from "../hooks/useIndexVersions";
import type { CorpusResponse, EvalRunListResponse, IndexVersionListResponse } from "../api/types";

vi.mock("../hooks/useEval");
vi.mock("../hooks/useIndexVersions");

vi.mock("../api/eval", () => ({
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

function makeRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "r1",
    workspace_id: "w1",
    eval_set_id: "e1",
    index_version_id: "v1",
    pipeline: { steps: ["retrieve", "generate"] },
    metrics: { recall_at_5: 0.8, mrr: 0.65, total_items: 10 },
    ts: "2026-01-01T12:00:00Z",
    ...overrides,
  };
}

function mockHooks(
  runsData?: EvalRunListResponse,
  versionsData?: IndexVersionListResponse,
) {
  vi.mocked(useEvalRuns).mockReturnValue({
    data: runsData,
    isLoading: false,
    error: null,
  } as ReturnType<typeof useEvalRuns>);
  vi.mocked(useIndexVersions).mockReturnValue({
    data: versionsData,
    isLoading: false,
    error: null,
  } as ReturnType<typeof useIndexVersions>);
  vi.mocked(useCreateEvalRun).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCreateEvalRun>);
  vi.mocked(useCompareEvalRuns).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCompareEvalRuns>);
}

describe("EvalRunsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders run list with metrics", () => {
    mockHooks(
      {
        items: [
          makeRun({
            id: "r1",
            metrics: { recall_at_5: 0.8, mrr: 0.65, total_items: 10 },
          }),
        ],
      },
      { versions: [], total: 0 },
    );

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("recall_at_5:")).toBeInTheDocument();
    expect(screen.getByText("0.8000")).toBeInTheDocument();
    expect(screen.getByText("mrr:")).toBeInTheDocument();
  });

  it("shows empty state when no runs", () => {
    mockHooks({ items: [] }, { versions: [], total: 0 });

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText(/Нет прогонов/)).toBeInTheDocument();
  });

  it("shows run button", () => {
    mockHooks({ items: [] }, { versions: [], total: 0 });

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("Запустить")).toBeInTheDocument();
  });

  it("hides compare button when fewer than 2 runs selected", () => {
    mockHooks(
      { items: [makeRun({ id: "r1" }), makeRun({ id: "r2" })] },
      { versions: [], total: 0 },
    );

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    expect(screen.queryByText(/Сравнить/)).not.toBeInTheDocument();
  });

  it("shows compare button when 2+ runs selected", () => {
    mockHooks(
      { items: [makeRun({ id: "r1" }), makeRun({ id: "r2" })] },
      { versions: [], total: 0 },
    );

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    // Select two runs
    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);
    fireEvent.click(checkboxes[1]);

    expect(screen.getByText(/Сравнить/)).toBeInTheDocument();
  });

  it("shows version ID prefix in run", () => {
    mockHooks(
      { items: [makeRun({ id: "r1", index_version_id: "v1-full-uuid" })] },
      { versions: [], total: 0 },
    );

    render(
      <EvalRunsPage
        corpus={makeCorpus()}
        evalSetId="e1"
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("Версия: v1-full-")).toBeInTheDocument();
  });
});
