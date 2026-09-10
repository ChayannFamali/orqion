import { apiFetch } from "./client";
import type { WorkspaceSettingListResponse, WorkspaceSettingResponse, WorkspaceSettingUpdate } from "./types";

/**
 * Реестр служебных настроек рабочей области.
 *
 * Чтение доступно всем аутентифицированным; право на изменение каждого
 * ключа отражено полем `editable` ответа. Запись идёт по одному ключу за
 * вызов — при отказе понятно, какое именно поле его вызвало.
 */
export async function apiGetWorkspaceSettings(): Promise<WorkspaceSettingListResponse> {
  return apiFetch<WorkspaceSettingListResponse>("/api/workspace/settings");
}

export async function apiUpdateWorkspaceSetting(
  body: WorkspaceSettingUpdate,
): Promise<WorkspaceSettingResponse> {
  return apiFetch<WorkspaceSettingResponse>("/api/workspace/settings", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
