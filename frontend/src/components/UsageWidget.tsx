import { useState } from "react";
import { ChevronDown, ChevronUp, Coins, Cpu } from "lucide-react";
import { useMyUsage } from "../hooks/useMyUsage";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function formatCost(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

function MetricBar({
  used,
  limit,
  label,
  icon,
  format,
}: {
  used: number;
  limit: number | null;
  label: string;
  icon: React.ReactNode;
  format: (n: number) => string;
}) {
  const unlimited = limit === null;
  const percent = unlimited ? 0 : Math.min(100, Math.round((used / limit) * 100));
  const overLimit = !unlimited && used > limit;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-xs">
        {icon}
        <span className="text-muted-foreground">{label}:</span>
        <span className="font-medium">
          {format(used)}
          {unlimited ? "" : ` / ${format(limit)}`}
        </span>
        {unlimited && (
          <span className="text-muted-foreground">(без лимита)</span>
        )}
      </div>
      {!unlimited && (
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full transition-all ${overLimit ? "bg-destructive" : "bg-primary"}`}
            style={{ width: `${percent}%` }}
          />
        </div>
      )}
    </div>
  );
}

export function UsageWidget() {
  const { data, isLoading, error } = useMyUsage();
  const [expanded, setExpanded] = useState(false);

  if (isLoading || error || !data) {
    return null;
  }

  return (
    <div className="relative">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent"
      >
        <Cpu className="h-3.5 w-3.5" />
        <span className="font-medium">{formatTokens(data.tokens_used)}</span>
        {data.tokens_limit !== null && (
          <span className="text-muted-foreground">
            / {formatTokens(data.tokens_limit)}
          </span>
        )}
        {expanded ? (
          <ChevronUp className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        )}
      </button>

      {expanded && (
        <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-border bg-background p-3 shadow-md">
          <div className="mb-2 text-xs font-medium text-muted-foreground">
            Расход за {data.period}
          </div>

          <div className="space-y-2">
            <MetricBar
              used={data.tokens_used}
              limit={data.tokens_limit}
              label="Токены"
              icon={<Cpu className="h-3 w-3 text-muted-foreground" />}
              format={formatTokens}
            />
            <MetricBar
              used={data.cost_used}
              limit={data.cost_limit}
              label="Стоимость"
              icon={<Coins className="h-3 w-3 text-muted-foreground" />}
              format={formatCost}
            />
          </div>

          {data.by_model.length > 0 && (
            <div className="mt-3 border-t border-border pt-2">
              <div className="mb-1 text-xs font-medium text-muted-foreground">
                По моделям:
              </div>
              <div className="space-y-1">
                {data.by_model.map((m) => (
                  <div
                    key={m.model_id}
                    className="flex justify-between text-xs text-muted-foreground"
                  >
                    <span className="truncate">{m.model_id}</span>
                    <span className="ml-2 flex-shrink-0">
                      {formatTokens(m.tokens_in + m.tokens_out)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
