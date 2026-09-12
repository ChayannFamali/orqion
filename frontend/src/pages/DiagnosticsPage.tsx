import { ExternalLink, Loader2 } from "lucide-react";
import { useEnvironmentDiagnostics } from "../hooks/useDiagnostics";
import type {
  DiskDiagnostics,
  ExternalServiceDiagnostics,
  GpuInfo,
  LocalComponentDiagnostics,
} from "../api/types";

/**
 * Диагностика окружения — только чтение состояния хоста (T-444, T-511).
 *
 * Намеренно без действий (arch.md §14.3): никаких кнопок
 * «скачать»/«установить» — установка и обновление драйверов
 * выполняются средствами ОС вне orqion.
 *
 * Каждый пункт деградирует самостоятельно: недоступность одного
 * не убирает остальные. «Ещё не проверялся» и «не используется в этой
 * конфигурации» показаны отдельными состояниями, а не как отказ.
 */

/** Статус внешнего сервиса по накопленному результату зонда. */
const SERVICE_STATUS_LABEL: Record<string, string> = {
  ok: "доступен",
  no_models: "нет доступных моделей",
  never_probed: "ещё не проверялся",
  disabled: "отключён",
  not_configured: "не настроен",
};

const SERVICE_ROLE_LABEL: Record<string, string> = {
  llm: "модель",
  embedder: "эмбеддинги",
};

function formatDiskSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
  if (bytes < 1024 * 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} ГБ`;
  return `${(bytes / (1024 * 1024 * 1024 * 1024)).toFixed(1)} ТБ`;
}

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} д ${hours} ч`;
  if (hours > 0) return `${hours} ч ${minutes} мин`;
  return `${minutes} мин`;
}

export function DiagnosticsPage() {
  const { data, isLoading, isError } = useEnvironmentDiagnostics();

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-5xl space-y-4">
        <div>
          <h2 className="text-xl font-bold">Диагностика окружения</h2>
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

        {data && (
          <div
            className="rounded-lg border border-border bg-card p-4"
            data-testid="diagnostics-host"
          >
            <h3 className="mb-3 text-base font-semibold">Хост</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-xs text-muted-foreground">ОС</dt>
                <dd>
                  {data.host.os_name} {data.host.os_version}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Python</dt>
                <dd>{data.host.python_version}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Время работы</dt>
                <dd>
                  {data.host.uptime_seconds != null
                    ? formatUptime(data.host.uptime_seconds)
                    : "неизвестно"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Запуск процесса</dt>
                <dd>
                  {data.host.started_at
                    ? new Date(data.host.started_at).toLocaleString()
                    : "неизвестно"}
                </dd>
              </div>
            </dl>

            {data.host.disks.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-xs text-muted-foreground">Свободное место</div>
                {data.host.disks.map((disk) => (
                  <DiskRow key={disk.label} disk={disk} />
                ))}
              </div>
            )}
          </div>
        )}

        {data && data.services.length > 0 && (
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="mb-1 text-base font-semibold">Внешние сервисы</h3>
            <p className="mb-3 text-xs text-muted-foreground">
              Статус берётся из последней проверки провайдера, которая выполняется
              периодически в фоне; этот раздел собственных запросов не делает.
            </p>
            <div className="space-y-2" data-testid="diagnostics-services">
              {data.services.map((service) => (
                <ServiceRow key={service.id} service={service} />
              ))}
            </div>
          </div>
        )}

        {data && data.components.length > 0 && (
          <div className="rounded-lg border border-border bg-card p-4">
            <h3 className="mb-3 text-base font-semibold">Локальные компоненты</h3>
            <div className="space-y-2" data-testid="diagnostics-components">
              {data.components.map((component) => (
                <ComponentRow key={component.name} component={component} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DiskRow({ disk }: { disk: DiskDiagnostics }) {
  return (
    <div className="rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">{disk.label}</span>
        <span className="text-muted-foreground">
          {disk.available && disk.free_bytes != null && disk.total_bytes != null
            ? `${formatDiskSize(disk.free_bytes)} из ${formatDiskSize(disk.total_bytes)}`
            : "недоступно"}
        </span>
      </div>
      <div className="mt-1 truncate text-xs text-muted-foreground" title={disk.path}>
        {disk.path}
        {disk.measured_path ? ` — измерено по ${disk.measured_path}` : ""}
      </div>
      {!disk.available && disk.reason && (
        <div className="mt-1 text-xs text-muted-foreground">{disk.reason}</div>
      )}
    </div>
  );
}

function ServiceRow({ service }: { service: ExternalServiceDiagnostics }) {
  const label = SERVICE_STATUS_LABEL[service.status] ?? service.status;
  return (
    <div className="rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">
          {service.kind ? `${service.kind} · ` : ""}
          {SERVICE_ROLE_LABEL[service.role] ?? service.role}
        </span>
        <span className="text-muted-foreground">{label}</span>
      </div>
      {service.base_url && (
        <div className="mt-1 truncate text-xs text-muted-foreground" title={service.base_url}>
          {service.base_url}
        </div>
      )}
      <div className="mt-1 text-xs text-muted-foreground">
        {service.last_probe_at
          ? `Проверен: ${new Date(service.last_probe_at).toLocaleString()}`
          : "Ещё не проверялся"}
        {service.available_model_count > 0 &&
          ` · моделей доступно: ${service.available_model_count}`}
        {service.model_count > 0 && ` · зарегистрировано: ${service.model_count}`}
      </div>
      {service.reason && (
        <div className="mt-1 text-xs text-muted-foreground">{service.reason}</div>
      )}
    </div>
  );
}

function ComponentRow({ component }: { component: LocalComponentDiagnostics }) {
  // null — не «отказ», а «не используется» или «ещё не создано»: причина
  // объясняет, почему значение неизвестно.
  const label =
    component.available == null
      ? "не используется"
      : component.available
        ? "доступно"
        : "недоступно";
  return (
    <div className="rounded-md border border-border bg-background p-3 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium">{component.name}</span>
        <span className="text-muted-foreground">{label}</span>
      </div>
      {(component.reason || component.detail) && (
        <div className="mt-1 text-xs text-muted-foreground">
          {[component.reason, component.detail].filter(Boolean).join(" · ")}
        </div>
      )}
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
