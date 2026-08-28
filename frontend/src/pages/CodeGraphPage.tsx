import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import type { Core, ElementDefinition } from "cytoscape";
import { Loader2 } from "lucide-react";
import { useCorpora } from "../hooks/useCorpora";
import { useCodeGraph } from "../hooks/useCodeGraph";
import type { CodeGraphResponse } from "../api/types";

/**
 * Т-504: граф связей кода (импорты/вызовы, MVP).
 *
 * Read-only визуализация по активной версии индекса выбранного корпуса:
 * узлы — символы/файлы/импортируемые модули, рёбра — «символ → родитель»
 * и «чанк → модуль». Усечение по числу узлов — только явное: при
 * превышении лимита показывается «показано N из M узлов».
 *
 * Визуализация — на базе графовой библиотеки с геометрическим поиском
 * узла под курсором и перетаскиванием из коробки; физика разлёта —
 * плагин силовой раскладки.
 */

const KIND_LABELS: Record<string, string> = {
  symbol: "символ",
  file: "файл",
  module: "импортируемый модуль",
};

const STYLESHEET = [
  {
    selector: "node",
    style: {
      "background-color": "#64748b",
      label: "data(name)",
      "font-size": 10,
      color: "#94a3b8",
      "text-valign": "bottom",
      "text-margin-y": 4,
      width: 14,
      height: 14,
      "border-width": 1,
      "border-color": "#1e293b",
      "overlay-opacity": 0,
    },
  },
  { selector: "node.symbol", style: { "background-color": "#3b82f6" } },
  { selector: "node.file", style: { "background-color": "#94a3b8" } },
  { selector: "node.module", style: { "background-color": "#f59e0b" } },
  {
    selector: "edge",
    style: {
      width: 1,
      "line-color": "#cbd5e1",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "#cbd5e1",
      "arrow-scale": 0.8,
      "curve-style": "bezier",
      opacity: 0.6,
    },
  },
  {
    selector: "edge.parent",
    style: { "line-color": "#93c5fd", "target-arrow-color": "#93c5fd" },
  },
  {
    selector: "edge.import",
    style: { "line-color": "#fcd34d", "target-arrow-color": "#fcd34d" },
  },
] as cytoscape.StylesheetJson;

let fcoseRegistered = false;

function registerFcose(): void {
  if (!fcoseRegistered) {
    cytoscape.use(fcose);
    fcoseRegistered = true;
  }
}

function buildElements(graph: CodeGraphResponse): ElementDefinition[] {
  const nodeIds = new Set(graph.nodes.map((n) => n.id));
  const elements: ElementDefinition[] = graph.nodes.map((n) => ({
    data: {
      id: n.id,
      name: n.label,
      kind: n.kind,
      file: n.file ?? "",
    },
    classes: n.kind,
  }));
  // Защита от рёбер на отсутствующие узлы (библиотека на таких падает).
  graph.edges.forEach((e, i) => {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return;
    elements.push({
      data: { id: `edge-${i}`, source: e.source, target: e.target },
      classes: e.kind,
    });
  });
  return elements;
}

interface TooltipState {
  x: number;
  y: number;
  lines: string[];
}

function CodeGraphCanvas({ graph }: { graph: CodeGraphResponse }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    registerFcose();
    const cy: Core = cytoscape({
      container: el,
      elements: buildElements(graph),
      style: STYLESHEET,
      wheelSensitivity: 0.2,
      minZoom: 0.2,
      maxZoom: 3,
    });

    cy.layout({
      name: "fcose",
      animate: true,
      animationDuration: 400,
      fit: true,
      padding: 30,
      nodeRepulsion: 6500,
      idealEdgeLength: 70,
      randomize: true,
    } as unknown as cytoscape.LayoutOptions).run();

    const onOver = (event: cytoscape.EventObject) => {
      const data = event.target.data() as {
        name?: string;
        kind?: string;
        file?: string;
      };
      const lines: string[] = [data.name ?? ""];
      if (data.kind && KIND_LABELS[data.kind]) lines.push(KIND_LABELS[data.kind]);
      if (data.file) lines.push(data.file);
      const pos = event.renderedPosition;
      setTooltip({ x: pos.x + 12, y: pos.y + 12, lines });
    };
    const onOut = () => setTooltip(null);

    cy.on("mouseover", "node", onOver);
    cy.on("mouseout", "node", onOut);
    cy.on("pan zoom", onOut);

    return () => {
      cy.destroy();
      setTooltip(null);
    };
  }, [graph]);

  return (
    <div className="relative min-h-0 flex-1" data-testid="code-graph-canvas">
      <div ref={containerRef} className="absolute inset-0" />
      {tooltip && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground shadow"
          style={{ left: tooltip.x, top: tooltip.y }}
          data-testid="code-graph-tooltip"
        >
          {tooltip.lines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      )}
      <div className="pointer-events-none absolute bottom-2 left-2 flex gap-3 rounded-md bg-background/80 px-2 py-1 text-xs text-muted-foreground">
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "#3b82f6" }}
          />{" "}
          символ
        </span>
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "#94a3b8" }}
          />{" "}
          файл
        </span>
        <span>
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "#f59e0b" }}
          />{" "}
          модуль
        </span>
      </div>
    </div>
  );
}

export function CodeGraphPage() {
  const { data: corporaData, isLoading: corporaLoading } = useCorpora();
  const corpora = corporaData?.corpora ?? [];
  const [selectedCorpusId, setSelectedCorpusId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedCorpusId === null && corpora.length > 0) {
      setSelectedCorpusId(corpora[0].id);
    }
  }, [corpora, selectedCorpusId]);

  const { data: graph, isLoading: graphLoading, isError } = useCodeGraph(selectedCorpusId);

  const hasContent = graph !== undefined && graph.nodes.length > 0;

  return (
    <div className="flex h-full flex-col overflow-hidden p-6">
      <div className="mb-4 flex items-center gap-3">
        <h2 className="text-xl font-bold">Граф связей кода</h2>
        {corpora.length > 0 && (
          <select
            value={selectedCorpusId ?? ""}
            onChange={(e) => setSelectedCorpusId(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            data-testid="code-graph-corpus-select"
          >
            {corpora.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {graph?.truncated && (
        <div
          className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          data-testid="code-graph-truncation"
        >
          Показано {graph.shown_nodes} из {graph.total_nodes} узлов
        </div>
      )}

      {graphLoading || corporaLoading ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Загрузка графа…</span>
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
          Не удалось загрузить граф.
        </div>
      ) : corpora.length === 0 ? (
        <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
          Нет корпусов — создайте корпус и соберите индекс.
        </div>
      ) : graph && !hasContent ? (
        <div
          className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground"
          data-testid="code-graph-empty"
        >
          {graph.index_version_id === null
            ? "Для корпуса нет активной версии индекса."
            : "В активной версии индекса нет кодовых фрагментов."}
        </div>
      ) : graph && hasContent ? (
        <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card">
          <CodeGraphCanvas graph={graph} />
        </div>
      ) : null}
    </div>
  );
}
