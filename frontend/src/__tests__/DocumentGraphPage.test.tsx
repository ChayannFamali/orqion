import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocumentGraphPage } from "../pages/DocumentGraphPage";
import { useCorpora } from "../hooks/useCorpora";
import { useDocumentGraph } from "../hooks/useDocumentGraph";
import type { DocumentGraphResponse } from "../api/types";

/**
 * Т-505: граф связей документов (семантические кластеры).
 *
 * Приёмка: деградация честная (``available=false`` → явная причина),
 * усечение только явное, пустые состояния понятны, узлы/рёбра уходят в
 * графовую библиотеку, экземпляр уничтожается при размонтировании.
 */

const cyMocks = vi.hoisted(() => {
  const instance = {
    layout: vi.fn(() => ({ run: vi.fn() })),
    destroy: vi.fn(),
    on: vi.fn(),
  };
  const factory = vi.fn((_options: unknown) => instance);
  return { instance, factory };
});

vi.mock("cytoscape", () => ({
  default: Object.assign(cyMocks.factory, { use: vi.fn() }),
}));
vi.mock("cytoscape-fcose", () => ({ default: vi.fn() }));

vi.mock("../hooks/useCorpora", () => ({
  useCorpora: vi.fn(),
}));
vi.mock("../hooks/useDocumentGraph", () => ({
  useDocumentGraph: vi.fn(),
}));

function mockCorpora(corpora: { id: string; name: string }[], loading = false) {
  vi.mocked(useCorpora).mockReturnValue({
    data: { corpora } as ReturnType<typeof useCorpora>["data"],
    isLoading: loading,
    isError: false,
  } as ReturnType<typeof useCorpora>);
}

function mockGraph(graph: Partial<DocumentGraphResponse> | undefined, loading = false) {
  vi.mocked(useDocumentGraph).mockReturnValue({
    data: graph as DocumentGraphResponse | undefined,
    isLoading: loading,
    isError: false,
  } as ReturnType<typeof useDocumentGraph>);
}

const GRAPH_FULL: DocumentGraphResponse = {
  corpus_id: "c1",
  index_version_id: "iv1",
  available: true,
  reason: null,
  nodes: [
    { id: "cluster:0", label: "Группа 1", kind: "cluster", document_id: null },
    { id: "doc:d1", label: "a.md", kind: "document", document_id: "d1" },
    { id: "doc:d2", label: "b.md", kind: "document", document_id: "d2" },
  ],
  edges: [
    { source: "cluster:0", target: "doc:d1", kind: "member" },
    { source: "cluster:0", target: "doc:d2", kind: "member" },
  ],
  total_documents: 2,
  shown_documents: 2,
  truncated: false,
  cluster_count: 1,
  from_cache: false,
};

describe("DocumentGraphPage (Т-505)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("передаёт узлы и рёбра в графовую библиотеку", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph(GRAPH_FULL);

    render(<DocumentGraphPage />);

    expect(cyMocks.factory).toHaveBeenCalled();
    const options = cyMocks.factory.mock.calls[cyMocks.factory.mock.calls.length - 1][0] as unknown as {
      elements: { data: { id?: string; source?: string } }[];
    };
    const nodeIds = options.elements.filter((e) => !e.data.source).map((e) => e.data.id);
    expect(nodeIds).toEqual(["cluster:0", "doc:d1", "doc:d2"]);
    expect(options.elements.filter((e) => e.data.source)).toHaveLength(2);
    expect(screen.getByTestId("document-graph-corpus-select")).toBeInTheDocument();
  });

  it("использует силовую раскладку и уничтожает экземпляр при размонтировании", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph(GRAPH_FULL);

    const { unmount } = render(<DocumentGraphPage />);

    expect(cyMocks.instance.layout).toHaveBeenCalledWith(
      expect.objectContaining({ name: "fcose" }),
    );
    unmount();
    expect(cyMocks.instance.destroy).toHaveBeenCalled();
  });

  it("деградация без экстры: явная причина вместо графика", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph({
      ...GRAPH_FULL,
      available: false,
      reason: "Граф документов недоступен: требуется дополнительный компонент.",
      nodes: [],
      edges: [],
    });

    render(<DocumentGraphPage />);

    expect(screen.getByTestId("document-graph-unavailable")).toHaveTextContent(
      "требуется дополнительный компонент",
    );
    expect(cyMocks.factory).not.toHaveBeenCalled();
  });

  it("усечение показывается явно: «построено по N из M документов»", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph({
      ...GRAPH_FULL,
      total_documents: 250,
      shown_documents: 200,
      truncated: true,
    });

    render(<DocumentGraphPage />);

    expect(screen.getByTestId("document-graph-truncation")).toHaveTextContent(
      "Кластеризация построена по 200 из 250 документов",
    );
  });

  it("без усечения баннера нет", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph(GRAPH_FULL);

    render(<DocumentGraphPage />);

    expect(screen.queryByTestId("document-graph-truncation")).not.toBeInTheDocument();
  });

  it("корпус без активной версии — понятное пустое состояние", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph({
      ...GRAPH_FULL,
      nodes: [],
      edges: [],
      index_version_id: null,
      total_documents: 0,
      shown_documents: 0,
    });

    render(<DocumentGraphPage />);

    expect(screen.getByTestId("document-graph-empty")).toHaveTextContent(
      "Для корпуса нет активной версии индекса.",
    );
  });

  it("показывает число групп", () => {
    mockCorpora([{ id: "c1", name: "docs" }]);
    mockGraph(GRAPH_FULL);

    render(<DocumentGraphPage />);

    expect(screen.getByText("Групп: 1")).toBeInTheDocument();
  });

  it("нет корпусов — подсказка про создание корпуса", () => {
    mockCorpora([]);
    mockGraph(undefined);

    render(<DocumentGraphPage />);

    expect(
      screen.getByText(/Нет корпусов — создайте корпус и соберите индекс/),
    ).toBeInTheDocument();
  });
});
