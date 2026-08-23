import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCreateModel, apiCreateProvider, apiDeleteModel, apiGetModelDownloadStatus, apiListProviders, apiProbeProvider, apiStartModelDownload, apiUpdateModel, apiUpdateProvider } from "../api/providers";
import { queryKeys } from "../api/query-keys";
import type { DownloadStatusResponse, ModelCreate, ModelUpdate, ProviderCreate, ProviderUpdate } from "../api/types";

/** Терминальные статусы скачивания (единый контракт, бэкенд T-437). */
export const TERMINAL_DOWNLOAD_STATUSES = new Set<DownloadStatusResponse["status"]>([
  "completed",
  "error",
  "already_downloaded",
]);

export function isTerminalDownloadStatus(
  status: DownloadStatusResponse["status"] | undefined,
): boolean {
  return status !== undefined && TERMINAL_DOWNLOAD_STATUSES.has(status);
}

export function useProviders() {
  return useQuery({
    queryKey: queryKeys.providers.all,
    queryFn: apiListProviders,
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderCreate) => apiCreateProvider(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, body }: { providerId: string; body: ProviderUpdate }) =>
      apiUpdateProvider(providerId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useProbeProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, deep = false }: { providerId: string; deep?: boolean }) =>
      apiProbeProvider(providerId, deep),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useCreateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, body }: { providerId: string; body: ModelCreate }) =>
      apiCreateModel(providerId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useUpdateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, body }: { modelId: string; body: ModelUpdate }) =>
      apiUpdateModel(modelId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

/** T-443 (коммит 2): удаление модели провайдера (опц. с очисткой диска). */
export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, deleteFromDisk }: { modelId: string; deleteFromDisk: boolean }) =>
      apiDeleteModel(modelId, deleteFromDisk),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

/** T-437, часть А: старт скачивания модели на локальный провайдер. */
export function useStartModelDownload() {
  return useMutation({
    mutationFn: ({ providerId, model }: { providerId: string; model: string }) =>
      apiStartModelDownload(providerId, model),
  });
}

/**
 * T-437, часть А: поллинг статуса скачивания.
 *
 * Активен пока есть живой job_id и статус не терминальный;
 * частота 2s по прецеденту useIndexVersions. Ответ старта может быть
 * уже терминальным (already_downloaded) — тогда поллинг не нужен.
 */
export function useModelDownloadStatus(
  providerId: string,
  jobId: string | null,
  initialStatus: DownloadStatusResponse["status"] | null,
) {
  return useQuery({
    queryKey: queryKeys.providers.downloadStatus(providerId, jobId ?? "none"),
    queryFn: () => apiGetModelDownloadStatus(providerId, jobId!),
    enabled: jobId !== null && !isTerminalDownloadStatus(initialStatus ?? undefined),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return isTerminalDownloadStatus(status) ? false : 2000;
    },
  });
}
