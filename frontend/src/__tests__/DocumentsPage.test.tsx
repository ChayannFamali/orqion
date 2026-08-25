import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DocumentsPage } from "../pages/DocumentsPage";
import { useDocuments, useDeleteDocument } from "../hooks/useDocuments";
import type { CorpusResponse, DocumentListResponse } from "../api/types";

vi.mock("../hooks/useDocuments");
vi.mock("../api/documents", () => ({
  apiListDocuments: vi.fn(),
  apiUploadDocument: vi.fn(),
  apiDeleteDocument: vi.fn(),
  parseError: vi.fn(),
}));

// Mock useQueryClient
vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQueryClient: () => ({
      invalidateQueries: vi.fn(),
    }),
  };
});

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

  it("shows pending_deletion badge for deferred-delete documents (BUG-020)", () => {
    mockHooks({
      documents: [makeDoc({ id: "d1", status: "pending_deletion" })],
      total: 1,
    });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText("pending_deletion")).toBeInTheDocument();
  });

  it("shows deferred delete notice when delete is postponed (BUG-020)", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      deleted: false,
      status: "pending_deletion",
      reason: "Документ помечен на удаление, но у него остаются чанки",
    });
    vi.mocked(useDocuments).mockReturnValue({
      data: { documents: [makeDoc({ id: "d1" })], total: 1 },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useDocuments>);
    vi.mocked(useDeleteDocument).mockReturnValue({
      isPending: false,
      mutateAsync,
    } as unknown as ReturnType<typeof useDeleteDocument>);

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["*"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText("Удалить документ"));
    await waitFor(() => {
      expect(screen.getByText("Удалить документ?")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Удалить", { selector: "button" }));

    await waitFor(() => {
      expect(screen.getByText(/Документ помечен на удаление/)).toBeInTheDocument();
    });
    expect(mutateAsync).toHaveBeenCalledWith("d1");
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

describe("DocumentsPage UploadModal (T-423)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("opens upload modal with multi-file drop zone", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    expect(screen.getByText("Загрузка документов")).toBeInTheDocument();
    expect(screen.getByText("Выбрать файлы")).toBeInTheDocument();
    expect(screen.getByText("Выбрать папку")).toBeInTheDocument();
  });

  it("shows file input with multiple attribute", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const inputs = document.querySelectorAll('input[type="file"]');
    expect(inputs.length).toBe(2); // files + folder
    expect(inputs[0].hasAttribute("multiple")).toBe(true);
    expect(inputs[1].hasAttribute("multiple")).toBe(true);
  });

  it("adds files to queue when selected via input", () => {
    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const input = document.querySelectorAll('input[type="file"]')[0];
    const file1 = new File(["content1"], "file1.py", { type: "text/plain" });
    const file2 = new File(["content2"], "file2.md", { type: "text/plain" });

    fireEvent.change(input, { target: { files: [file1, file2] } });

    expect(screen.getByText("file1.py")).toBeInTheDocument();
    expect(screen.getByText("file2.md")).toBeInTheDocument();
    expect(screen.getByText("Начать загрузку")).toBeInTheDocument();
  });

  it("shows summary after all uploads complete", async () => {
    const { apiUploadDocument } = await import("../api/documents");
    vi.mocked(apiUploadDocument).mockResolvedValue({
      id: "d1",
      corpus_id: "c1",
      filename: "file1.py",
      mime: "text/plain",
      sha256: "abc",
      blob_uri: "blob1",
      source_type: "upload",
      status: "pending",
      error: null,
      uploaded_at: "2026-01-01T00:00:00Z",
    });

    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const input = document.querySelectorAll('input[type="file"]')[0];
    const file = new File(["content"], "test.py", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(screen.getByText("Начать загрузку"));

    await waitFor(() => {
      expect(screen.getByText(/загружено/)).toBeInTheDocument();
    });

    expect(screen.getByText("Закрыть")).toBeInTheDocument();
  });

  it("shows error status for failed upload", async () => {
    const { apiUploadDocument } = await import("../api/documents");
    vi.mocked(apiUploadDocument).mockRejectedValue({
      error: "file_type_not_allowed",
      reason: "Недопустимый тип файла",
      constraint: null,
      hint: null,
    });

    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const input = document.querySelectorAll('input[type="file"]')[0];
    const file = new File(["content"], "bad.exe", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(screen.getByText("Начать загрузку"));

    await waitFor(() => {
      expect(screen.getByText("Недопустимый тип файла")).toBeInTheDocument();
    });
  });

  it("one file failure does not block others", async () => {
    const { apiUploadDocument } = await import("../api/documents");
    vi.mocked(apiUploadDocument)
      .mockResolvedValueOnce({
        id: "d1",
        corpus_id: "c1",
        filename: "ok.py",
        mime: "text/plain",
        sha256: "abc",
        blob_uri: "blob1",
        source_type: "upload",
        status: "pending",
        error: null,
        uploaded_at: "2026-01-01T00:00:00Z",
      })
      .mockRejectedValueOnce({
        error: "file_too_large",
        reason: "Файл слишком большой",
        constraint: null,
        hint: null,
      });

    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const input = document.querySelectorAll('input[type="file"]')[0];
    const file1 = new File(["ok"], "ok.py", { type: "text/plain" });
    const file2 = new File(["big"], "big.bin", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file1, file2] } });

    fireEvent.click(screen.getByText("Начать загрузку"));

    await waitFor(() => {
      expect(screen.getByText(/загружено/)).toBeInTheDocument();
    });

    // One success, one error — both visible
    expect(screen.getByText("ok.py")).toBeInTheDocument();
    expect(screen.getByText("big.bin")).toBeInTheDocument();
    expect(screen.getByText("Файл слишком большой")).toBeInTheDocument();
    // Summary shows both counts
    expect(screen.getByText(/1 загружено/)).toBeInTheDocument();
    expect(screen.getByText(/1 ошибок/)).toBeInTheDocument();
  });

  it("cancel button sets files to cancelled status", async () => {
    const { apiUploadDocument } = await import("../api/documents");
    // Make upload hang forever so we can cancel mid-flight
    vi.mocked(apiUploadDocument).mockImplementation(
      () => new Promise(() => {}), // never resolves
    );

    mockHooks({ documents: [], total: 0 });

    render(
      <DocumentsPage
        corpus={makeCorpus()}
        capabilities={["upload"]}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByText("Загрузить"));

    const input = document.querySelectorAll('input[type="file"]')[0];
    const file = new File(["content"], "cancel.py", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    fireEvent.click(screen.getByText("Начать загрузку"));

    // Wait for "Отменить" button to appear (uploading in progress)
    await waitFor(() => {
      expect(screen.getByText("Отменить")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Отменить"));

    await waitFor(() => {
      expect(screen.getByText(/отменено/)).toBeInTheDocument();
    });

    // Cancelled count in summary, not error count
    expect(screen.getByText(/1 отменено/)).toBeInTheDocument();
  });
});
