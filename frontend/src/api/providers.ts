import { apiFetch } from "./client";
import type { DownloadStatusResponse, ModelCreate, ModelDeleteResponse, ModelResponse, ModelUpdate, ProbeResult, ProviderCreate, ProviderListResponse, ProviderResponse, ProviderUpdate } from "./types";

export async function apiListProviders(): Promise<ProviderListResponse> {
  return apiFetch<ProviderListResponse>("/api/providers");
}

export async function apiCreateProvider(body: ProviderCreate): Promise<ProviderResponse> {
  return apiFetch<ProviderResponse>("/api/providers", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateProvider(
  providerId: string,
  body: ProviderUpdate,
): Promise<ProviderResponse> {
  return apiFetch<ProviderResponse>(`/api/providers/${providerId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function apiProbeProvider(
  providerId: string,
  deep: boolean = false,
): Promise<ProbeResult> {
  const query = deep ? "?deep=true" : "";
  return apiFetch<ProbeResult>(`/api/providers/${providerId}/probe${query}`, {
    method: "POST",
  });
}

export async function apiCreateModel(
  providerId: string,
  body: ModelCreate,
): Promise<ModelResponse> {
  return apiFetch<ModelResponse>(`/api/providers/${providerId}/models`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateModel(
  modelId: string,
  body: ModelUpdate,
): Promise<ModelResponse> {
  return apiFetch<ModelResponse>(`/api/providers/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/**
 * T-443 (коммит 2): удаление модели провайдера.
 * `deleteFromDisk` — опциональная очистка с диска (только по явному
 * подтверждению в UI); ошибка диска не блокирует удаление метаданных.
 */
export async function apiDeleteModel(
  modelId: string,
  deleteFromDisk = false,
): Promise<ModelDeleteResponse> {
  const query = deleteFromDisk ? "?delete_from_disk=true" : "";
  return apiFetch<ModelDeleteResponse>(`/api/providers/models/${modelId}${query}`, {
    method: "DELETE",
  });
}

/** T-437: старт скачивания модели на локальный провайдер (единый контракт). */
export async function apiStartModelDownload(
  providerId: string,
  model: string,
): Promise<DownloadStatusResponse> {
  return apiFetch<DownloadStatusResponse>(
    `/api/providers/${providerId}/download-models`,
    {
      method: "POST",
      body: JSON.stringify({ model }),
    },
  );
}

/** T-437: статус скачивания (поллинг до терминального статуса). */
export async function apiGetModelDownloadStatus(
  providerId: string,
  jobId: string,
): Promise<DownloadStatusResponse> {
  return apiFetch<DownloadStatusResponse>(
    `/api/providers/${providerId}/download-status/${jobId}`,
  );
}
