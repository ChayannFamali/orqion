import { apiFetch } from "./client";
import type { ModelCreate, ModelResponse, ModelUpdate, ProbeResult, ProviderCreate, ProviderListResponse, ProviderResponse, ProviderUpdate } from "./types";

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
