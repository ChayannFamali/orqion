import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { IndexVersionsPage } from "../pages/IndexVersionsPage";
import {
  useIndexVersions,
  useBuildIndexVersion,
  useActivateIndexVersion,
  useRollbackIndexVersion,
  useCleanupRetiredVersions,
} from "../hooks/useIndexVersions";
import type { CorpusResponse, IndexVersionListResponse } from "../api/types";

vi.mock("../hooks/useIndexVersions");

vi.mock("../api/indexVersions", () => ({
  apiBuildIndexVersion: vi.fn(),
  apiListIndexVersions: vi.fn(),
  apiGetIndexVersion: vi.fn(),
  apiActivateIndexVersion: vi.fn(),
  apiRollbackIndexVersion: vi.fn(),
  apiCleanupRetiredVersions: vi.fn(),
}));

function makeCorpus(): CorpusResponse {
  return {
    id: "c1",
    name: "public",
    data_class: "К0",
    pinned_model_id: null,
    active_index_version_id: "v1",
  };
}

function makeVersion(overrides: Record<string, unknown> = {}) {
  return {
    id: "v1",
    corpus_id: "c1",
    embedding_model: "BAAI/bge-m3",
    chunker: "mixed-v1",
    chunker_version: "1.0",
    status: "completed",
    stats: { status: "completed", documents_done: 5, documents_total: 5, chunks_total: 20 },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mockHooks(
  versionsData?: IndexVersionListResponse,
  error: unknown = null,
) {
  vi.mocked(useIndexVersions).mockReturnValue({
    data: versionsData,
    isLoading: false,
    error,
  } as ReturnType<typeof useIndexVersions>);
  vi.mocked(useBuildIndexVersion).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useBuildIndexVersion>);
  vi.mocked(useActivateIndexVersion).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useActivateIndexVersion>);
  vi.mocked(useRollbackIndexVersion).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useRollbackIndexVersion>);
  vi.mocked(useCleanupRetiredVersions).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCleanupRetiredVersions>);
}

describe("IndexVersionsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders version list with status badges", () => {
    mockHooks({
      versions: [
        makeVersion({ id: "v1", status: "active" }),
        makeVersion({ id: "v2", status: "completed" }),
      ],
      total: 2,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getAllByText(/BAAI\/bge-m3/)).toHaveLength(2);
    expect(screen.getAllByText(/mixed-v1/)).toHaveLength(2);
  });

  it("shows empty state", () => {
    mockHooks({ versions: [], total: 0 });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText(/Нет версий индекса/)).toBeInTheDocument();
  });

  it("shows error state", () => {
    mockHooks(undefined, new Error("fail"));

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Ошибка загрузки версий")).toBeInTheDocument();
  });

  it("shows build button", () => {
    mockHooks({ versions: [], total: 0 });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Собрать")).toBeInTheDocument();
  });

  it("shows rollback button when active version exists", () => {
    mockHooks({
      versions: [makeVersion({ id: "v1", status: "active" })],
      total: 1,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Откатить")).toBeInTheDocument();
  });

  it("hides rollback button when no active version", () => {
    mockHooks({
      versions: [makeVersion({ id: "v1", status: "completed" })],
      total: 1,
    });
    const corpus = makeCorpus();
    corpus.active_index_version_id = null;

    render(<IndexVersionsPage corpus={corpus} onBack={vi.fn()} />);

    expect(screen.queryByText("Откатить")).not.toBeInTheDocument();
  });

  it("shows activate button for completed versions", () => {
    mockHooks({
      versions: [
        makeVersion({ id: "v1", status: "completed" }),
        makeVersion({ id: "v2", status: "active" }),
      ],
      total: 2,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Активировать")).toBeInTheDocument();
  });

  it("shows cleanup button when retired versions exist", () => {
    mockHooks({
      versions: [
        makeVersion({ id: "v1", status: "active" }),
        makeVersion({ id: "v2", status: "retired" }),
      ],
      total: 2,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Очистить retired")).toBeInTheDocument();
  });

  it("shows progress info for building version", () => {
    mockHooks({
      versions: [
        makeVersion({
          id: "v1",
          status: "building",
          stats: {
            status: "building",
            documents_done: 2,
            documents_total: 5,
            chunks_total: 8,
            current_document: "doc1.pdf",
          },
        }),
      ],
      total: 1,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText("Обработка: doc1.pdf")).toBeInTheDocument();
  });

  it("shows error info for interrupted version", () => {
    mockHooks({
      versions: [
        makeVersion({
          id: "v1",
          status: "interrupted",
          stats: { status: "interrupted", error: "Embedding failed" },
        }),
      ],
      total: 1,
    });

    render(<IndexVersionsPage corpus={makeCorpus()} onBack={vi.fn()} />);

    expect(screen.getByText(/Embedding failed/)).toBeInTheDocument();
  });
});
