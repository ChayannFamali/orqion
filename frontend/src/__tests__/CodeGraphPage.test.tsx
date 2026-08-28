import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { CodeGraphPage } from "../pages/CodeGraphPage";
import { useCorpora } from "../hooks/useCorpora";
import { useCodeGraph } from "../hooks/useCodeGraph";
import type { CodeGraphResponse } from "../api/types";

/**
 * Т-504: граф связей кода.
 *
 * Приёмка: усечение только явное («показано N из M узлов»), пустые
 * состояния понятны, графовая библиотека получает узлы/рёбра и силовую
 * раскладку, экземпляр уничтожается при размонтировании.
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
vi.mock("../hooks/useCodeGraph", () => ({
  useCodeGraph: vi.fn(),
}));

function mockCorpora(corpora: { id: string; name: string }[], loading = false) {
  vi.mocked(useCorpora).mockReturnValue({
    data: { corpora } as ReturnType<typeof useCorpora>["data"],
    isLoading: loading,
    isError: false,
  } as ReturnType<typeof useCorpora>);
}

function mockGraph(graph: Partial<CodeGraphResponse> | undefined, loading = false) {
  vi.mocked(useCodeGraph).mockReturnValue({
    data: graph as CodeGraphResponse | undefined,
    isLoading: loading,
    isError: false,
  } as ReturnType<typeof useCodeGraph>);
}

const GRAPH_FULL: CodeGraphResponse = {
  corpus_id: "c1",
  index_version_id: "iv1",
  nodes: [
    { id: "chunk:a", label: "MyClass", kind: "symbol", file: "app.py", language: "python" },
    { id: "module:os", label: "os", kind: "module", file: null, language: null },
  ],
  edges: [{ source: "chunk:a", target: "module:os", kind: "import" }],
  total_nodes: 2,
  shown_nodes: 2,
  truncated: false,
};

describe("CodeGraphPage (T-504)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("передаёт узлы и рёбра в графовую библиотеку", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph(GRAPH_FULL);

    render(<CodeGraphPage />);

    expect(cyMocks.factory).toHaveBeenCalled();
    const options = cyMocks.factory.mock.calls[cyMocks.factory.mock.calls.length - 1][0] as unknown as {
      elements: { data: { id?: string; source?: string } }[];
    };
    const nodeIds = options.elements
      .filter((e) => !e.data.source)
      .map((e) => e.data.id);
    expect(nodeIds).toEqual(["chunk:a", "module:os"]);
    expect(options.elements.filter((e) => e.data.source)).toHaveLength(1);
    expect(screen.getByTestId("code-graph-corpus-select")).toBeInTheDocument();
  });

  it("использует силовую раскладку и уничтожает экземпляр при размонтировании", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph(GRAPH_FULL);

    const { unmount } = render(<CodeGraphPage />);

    expect(cyMocks.instance.layout).toHaveBeenCalledWith(
      expect.objectContaining({ name: "fcose" }),
    );
    unmount();
    expect(cyMocks.instance.destroy).toHaveBeenCalled();
  });

  it("усечение показывается явно: «показано N из M узлов»", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph({
      ...GRAPH_FULL,
      total_nodes: 305,
      shown_nodes: 300,
      truncated: true,
    });

    render(<CodeGraphPage />);

    expect(screen.getByTestId("code-graph-truncation")).toHaveTextContent(
      "Показано 300 из 305 узлов",
    );
  });

  it("без усечения баннера нет", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph(GRAPH_FULL);

    render(<CodeGraphPage />);

    expect(screen.queryByTestId("code-graph-truncation")).not.toBeInTheDocument();
  });

  it("корпус без активной версии — понятное пустое состояние", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph({ ...GRAPH_FULL, nodes: [], edges: [], index_version_id: null, total_nodes: 0, shown_nodes: 0 });

    render(<CodeGraphPage />);

    expect(screen.getByTestId("code-graph-empty")).toHaveTextContent(
      "Для корпуса нет активной версии индекса.",
    );
  });

  it("активная версия без кодовых фрагментов — своё сообщение", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph({ ...GRAPH_FULL, nodes: [], edges: [], total_nodes: 0, shown_nodes: 0 });

    render(<CodeGraphPage />);

    expect(screen.getByTestId("code-graph-empty")).toHaveTextContent(
      "В активной версии индекса нет кодовых фрагментов.",
    );
  });

  it("нет корпусов — подсказка про создание корпуса", () => {
    mockCorpora([]);
    mockGraph(undefined);

    render(<CodeGraphPage />);

    expect(
      screen.getByText(/Нет корпусов — создайте корпус и соберите индекс/),
    ).toBeInTheDocument();
  });
});
