import { FileText, AlertTriangle } from "lucide-react";
import type { SourceEntry } from "../api/types";

interface SourceListProps {
  sources: SourceEntry[];
  ragDegraded?: boolean;
  ragErrors?: string[];
}

export function SourceList({ sources, ragDegraded, ragErrors }: SourceListProps) {
  if (sources.length === 0 && !ragDegraded) return null;

  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3">
      {ragDegraded && (
        <div className="flex items-start gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Поиск по корпусу выполнен в режиме деградации
            {ragErrors && ragErrors.length > 0 ? `: ${ragErrors.join(", ")}` : ""}
          </span>
        </div>
      )}
      {sources.length > 0 && (
        <>
          <div className="text-xs font-medium text-muted-foreground">Источники:</div>
          <ul className="space-y-1">
            {sources.map((source) => (
              <li key={source.chunk_id}>
                <a
                  href={`/api/documents/${source.document_id}/content`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-start gap-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
                >
                  <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>{source.structural_path}</span>
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
