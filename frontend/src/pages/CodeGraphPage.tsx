import { useEffect, useMemo, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
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
 */

interface GraphNode {
  id: string;
  name: string;
  kind: string;
  file?: string | null;
  language?: string | null;
}

interface GraphLink {
  source: string;
  target: string;
  kind: string;
}

const NODE_COLORS: Record<string, string> = {
  symbol: "#3b82f6",
  file: "#94a3b8",
  module: "#f59e0b",
};

const LINK_COLORS: Record<string, string> = {
  parent: "#93c5fd",
  import: "#fcd34d",
};

function toGraphData(graph: CodeGraphResponse) {
  const nodes: GraphNode[] = graph.nodes.map((n) => ({
    id: n.id,
    name: n.label,
    kind: n.kind,
    file: n.file,
    language: n.language,
  }));
  const links: GraphLink[] = graph.edges.map((e) => ({
    source: e.source,
    target: e.target,
    kind: e.kind,
  }));
  return { nodes, links };
}

function nodeLabel(node: GraphNode): string {
  const parts = [node.name];
  if (node.kind === "module") parts.push("импортируемый модуль");
  if (node.file) parts.push(node.file);
  return parts.join("\n");
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

  const graphData = useMemo(() => (graph ? toGraphData(graph) : null), [graph]);

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
      ) : graph && graph.nodes.length === 0 ? (
        <div
          className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground"
          data-testid="code-graph-empty"
        >
          {graph.index_version_id === null
            ? "Для корпуса нет активной версии индекса."
            : "В активной версии индекса нет кодовых фрагментов."}
        </div>
      ) : graphData ? (
        <div className="relative min-h-0 flex-1 rounded-lg border border-border bg-card" data-testid="code-graph-canvas">
          <ForceGraph2D
            graphData={graphData}
            nodeLabel={nodeLabel}
            nodeColor={(n) => NODE_COLORS[(n as GraphNode).kind] ?? "#64748b"}
            linkColor={(l) => LINK_COLORS[(l as GraphLink).kind] ?? "#cbd5e1"}
            nodeRelSize={5}
            linkDirectionalArrowLength={4}
            cooldownTicks={100}
          />
          <div className="pointer-events-none absolute bottom-2 left-2 flex gap-3 rounded-md bg-background/80 px-2 py-1 text-xs text-muted-foreground">
            <span>
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: NODE_COLORS.symbol }} /> символ
            </span>
            <span>
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: NODE_COLORS.file }} /> файл
            </span>
            <span>
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: NODE_COLORS.module }} /> модуль
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
