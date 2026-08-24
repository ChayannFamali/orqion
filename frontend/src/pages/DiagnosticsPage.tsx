import { ExternalLink, Loader2 } from "lucide-react";
import { useEnvironmentDiagnostics } from "../hooks/useDiagnostics";
import type { GpuInfo } from "../api/types";

/**
 * T-444: диагностика окружения — только чтение состояния хоста.
 *
 * Намеренно без действий (arch.md §14.3): никаких кнопок
 * «скачать»/«установить» — установка и обновление драйверов
 * выполняются средствами ОС вне orqion.
 */
export function DiagnosticsPage() {
  const { data, isLoading, isError } = useEnvironmentDiagnostics();

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-5xl space-y-4">
        <div>
          <h2 className="text-xl font-bold">Диагностика окружения</h2>
          <p className="text-sm text-muted-foreground">
            Только чтение: состояние хоста, на котором запущен orqion. Управление
            драйверами и backend инференса выполняется вне orqion.
          </p>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span>Загрузка диагностики…</span>
          </div>
        )}

        {isError && (
          <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">
            Не удалось получить данные диагностики.
          </div>
        )}

        {data && (
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="mb-3 text-base font-semibold">GPU (NVIDIA)</h3>
            {data.nvidia.available ? (
              <div className="space-y-3" data-testid="diagnostics-nvidia-available">
                <div className="text-sm">
                  Версия драйвера:{" "}
                  <span className="font-medium">
                    {data.nvidia.driver_version ?? "недоступно"}
                  </span>
                </div>
                <div className="space-y-2">
                  {data.nvidia.gpus.map((gpu, i) => (
                    <GpuCard key={i} gpu={gpu} index={i} />
                  ))}
                </div>
                {data.vendor_url && (
                  <a
                    href={data.vendor_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
                  >
                    Страница драйверов NVIDIA
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>
            ) : (
              <div className="text-sm text-muted-foreground" data-testid="diagnostics-unavailable">
                Недоступно{data.nvidia.reason ? `: ${data.nvidia.reason}` : ""}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function GpuCard({ gpu, index }: { gpu: GpuInfo; index: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="mb-2 text-sm font-medium">
        {gpu.name ?? `GPU ${index + 1}`}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">VRAM</dt>
          <dd>
            {gpu.memory_used_mib != null && gpu.memory_total_mib != null
              ? `${gpu.memory_used_mib} / ${gpu.memory_total_mib} MiB`
              : "недоступно"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Температура</dt>
          <dd>{gpu.temperature_c != null ? `${gpu.temperature_c} °C` : "недоступно"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Загрузка</dt>
          <dd>
            {gpu.utilization_percent != null ? `${gpu.utilization_percent}%` : "недоступно"}
          </dd>
        </div>
      </dl>
    </div>
  );
}
