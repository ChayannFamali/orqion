import { apiFetch } from "./client";
import type { RagSettingsResponse, RagSettingsUpdate } from "./types";

/** Т-506: настройки RAG-поиска уровня рабочей области (чтение — всем). */
export async function apiGetRagSettings(): Promise<RagSettingsResponse> {
  return apiFetch<RagSettingsResponse>("/api/rag-settings");
}

/** Т-506: изменение настроек RAG-поиска (право управления корпусами). */
export async function apiUpdateRagSettings(
  body: RagSettingsUpdate,
): Promise<RagSettingsResponse> {
  return apiFetch<RagSettingsResponse>("/api/rag-settings", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}
