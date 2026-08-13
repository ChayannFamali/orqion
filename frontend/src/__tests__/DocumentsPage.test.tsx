import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocumentsPage } from "../pages/DocumentsPage";
import { useDocuments, useUploadDocument, useDeleteDocument } from "../hooks/useDocuments";
import type { CorpusResponse, DocumentListResponse } from "../api/types";

vi.mock("../hooks/useDocuments");

vi.mock("../api/documents", () => ({
  apiListDocuments: vi.fn(),
  apiUploadDocument: vi.fn(),
  apiDeleteDocument: vi.fn(),
  parseError: vi.fn(),
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

function makeDoc(overrides: Record<string, unknown> = {}) {
  return {
    id: "d1",
    corpus_id: "c1",
    filename: "test.txt",
    mime: "text/plain",
    sha256: "abc123",
    blob_uri: "blob1",
    source_type: "upload",
    status: "pending",
    error: null,
    uploaded_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mockHooks(
  docsData?: DocumentListResponse,
  error: unknown = null,
) {
  vi.mocked(useDocuments).mockReturnValue({
    data: docsData,
    isLoading: false,
    error,
  } as ReturnType<typeof useDocuments>);
  vi.mocked(useUploadDocument).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useUploadDocument>);
  vi.mocked(useDeleteDocument).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useDeleteDocument>);
}

describe("DocumentsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders document list with status badges", () => {
    mockHooks({
      documents: [
        makeDoc({ id: "d1", filename: "a.txt", status: "pending" }),
        makeDoc({ id: "d2", filename: "b.md", status: "ready" }),
      ],
      total: 2,
    });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("a.txt")).toBeInTheDocument();
    expect(screen.getByText("b.md")).toBeInTheDocument();
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
  });

  it("shows empty state when no documents", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText(/Нет документов/)).toBeInTheDocument();
  });

  it("shows error state", () => {
    mockHooks(undefined, new Error("fail"));

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("Ошибка загрузки документов")).toBeInTheDocument();
  });

  it("shows error message for failed document", () => {
    mockHooks({
      documents: [
        makeDoc({ id: "d1", filename: "bad.pdf", status: "failed", error: "OCR required" }),
      ],
      total: 1,
    });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("OCR required")).toBeInTheDocument();
  });

  it("shows manage buttons for admin capabilities", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("Версии индекса")).toBeInTheDocument();
    expect(screen.getByText("Оценка качества")).toBeInTheDocument();
  });

  it("hides manage buttons for developer capabilities", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["chat", "upload"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.queryByText("Версии индекса")).not.toBeInTheDocument();
    expect(screen.queryByText("Оценка качества")).not.toBeInTheDocument();
  });
});
