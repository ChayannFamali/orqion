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
 * состояния понятны, библиотека визуализации получает узлы/рёбра.
 */

vi.mock("../hooks/useCorpora", () => ({
  useCorpora: vi.fn(),
}));
vi.mock("../hooks/useCodeGraph", () => ({
  useCodeGraph: vi.fn(),
}));
// Канвас в jsdom отсутствует — компонент подменяется заглушкой,
// пробрасывающей данные графа для проверок.
vi.mock("react-force-graph-2d", () => ({
  default: ({ graphData }: { graphData: { nodes: unknown[]; links: unknown[] } }) => (
    <div data-testid="force-graph" data-nodes={graphData.nodes.length} data-links={graphData.links.length} />
  ),
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

  it("рендерит граф и передаёт узлы/рёбра библиотеке", () => {
    mockCorpora([{ id: "c1", name: "code" }]);
    mockGraph(GRAPH_FULL);

    render(<CodeGraphPage />);

    const canvas = screen.getByTestId("force-graph");
    expect(canvas.getAttribute("data-nodes")).toBe("2");
    expect(canvas.getAttribute("data-links")).toBe("1");
    expect(screen.getByTestId("code-graph-corpus-select")).toBeInTheDocument();
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
