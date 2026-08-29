import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import type { Core, ElementDefinition } from "cytoscape";
import { Loader2 } from "lucide-react";
import { useCorpora } from "../hooks/useCorpora";
import { useDocumentGraph } from "../hooks/useDocumentGraph";
import type { DocumentGraphResponse } from "../api/types";

/**
 * Т-505: граф связей документов (семантические кластеры).
 *
 * Read-only визуализация по активной версии индекса выбранного корпуса:
 * узлы-группы («Группа N») и узлы-документы с рёбрами принадлежности.
 * Число групп задаёт администратор в настройках; названия без автогенерации.
 *
 * Деградация честная (паттерн Т-444): если опциональная зависимость
 * (экстра orqion[graph]) не установлена, сервер отвечает ``available=false``
 * с явной причиной — страница показывает её вместо графика, не падая.
 *
 * Усечение по числу документов — только явное: при превышении лимита
 * баннер «показано N из M документов».
 */

// Палитра групп — различимые цвета для узлов-кластеров.
const CLUSTER_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#14b8a6",
  "#f97316",
  "#ec4899",
];

function clusterColor(clusterId: string): string {
  // Детерминированный цвет по id узла-группы.
  let hash = 0;
  for (let i = 0; i < clusterId.length; i++) {
    hash = (hash * 31 + clusterId.charCodeAt(i)) >>> 0;
  }
  return CLUSTER_COLORS[hash % CLUSTER_COLORS.length];
}

let fcoseRegistered = false;

function registerFcose(): void {
  if (!fcoseRegistered) {
    cytoscape.use(fcose);
    fcoseRegistered = true;
  }
}

function buildElements(graph: DocumentGraphResponse): ElementDefinition[] {
  const nodeIds = new Set(graph.nodes.map((n) => n.id));
  const elements: ElementDefinition[] = graph.nodes.map((n) => ({
    data: {
      id: n.id,
      name: n.label,
      kind: n.kind,
      color: n.kind === "cluster" ? clusterColor(n.id) : "#64748b",
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

function DocumentGraphCanvas({ graph }: { graph: DocumentGraphResponse }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    registerFcose();
    const cy: Core = cytoscape({
      container: el,
      elements: buildElements(graph),
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(name)",
            "font-size": 11,
            color: "#94a3b8",
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: 16,
            height: 16,
            "border-width": 1,
            "border-color": "#1e293b",
            "overlay-opacity": 0,
          },
        },
        { selector: "node.cluster", style: { width: 26, height: 26, "font-weight": "bold" } },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": "#475569",
            "curve-style": "bezier",
            opacity: 0.5,
          },
        },
      ] as cytoscape.StylesheetJson,
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
      nodeRepulsion: 8000,
      idealEdgeLength: 90,
      randomize: true,
    } as unknown as cytoscape.LayoutOptions).run();

    const onOver = (event: cytoscape.EventObject) => {
      const data = event.target.data() as { name?: string; kind?: string };
      const lines: string[] = [data.name ?? ""];
      if (data.kind === "cluster") lines.push("группа документов");
      if (data.kind === "document") lines.push("документ");
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
    <div className="relative min-h-0 flex-1" data-testid="document-graph-canvas">
      <div ref={containerRef} className="absolute inset-0" />
      {tooltip && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground shadow"
          style={{ left: tooltip.x, top: tooltip.y }}
          data-testid="document-graph-tooltip"
        >
          {tooltip.lines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export function DocumentGraphPage() {
  const { data: corporaData, isLoading: corporaLoading } = useCorpora();
  const corpora = corporaData?.corpora ?? [];
  const [selectedCorpusId, setSelectedCorpusId] = useState<string | null>(null);

  useEffect(() => {
    if (selectedCorpusId === null && corpora.length > 0) {
      setSelectedCorpusId(corpora[0].id);
    }
  }, [corpora, selectedCorpusId]);

  const { data: graph, isLoading: graphLoading, isError } = useDocumentGraph(selectedCorpusId);

  const clusterNodes = useMemo(
    () => (graph ? graph.nodes.filter((n) => n.kind === "cluster") : []),
    [graph],
  );
  const hasContent = graph !== undefined && graph.available && graph.nodes.length > 0;

  return (
    <div className="flex h-full flex-col overflow-hidden p-6">
      <div className="mb-4 flex items-center gap-3">
        <h2 className="text-xl font-bold">Граф связей документов</h2>
        {corpora.length > 0 && (
          <select
            value={selectedCorpusId ?? ""}
            onChange={(e) => setSelectedCorpusId(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            data-testid="document-graph-corpus-select"
          >
            {corpora.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {graph && !graph.available && (
        <div
          className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          data-testid="document-graph-unavailable"
        >
          {graph.reason ?? "Граф документов недоступен."}
        </div>
      )}

      {graph?.truncated && (
        <div
          className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
          data-testid="document-graph-truncation"
        >
          Кластеризация построена по {graph.shown_documents} из {graph.total_documents} документов
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
      ) : graph && !graph.available ? null : graph && !hasContent ? (
        <div
          className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground"
          data-testid="document-graph-empty"
        >
          {graph.index_version_id === null
            ? "Для корпуса нет активной версии индекса."
            : "В активной версии индекса нет документов для кластеризации."}
        </div>
      ) : graph && hasContent ? (
        <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card">
          <DocumentGraphCanvas graph={graph} />
          <div className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
            Групп: {clusterNodes.length}
          </div>
        </div>
      ) : null}
    </div>
  );
}
