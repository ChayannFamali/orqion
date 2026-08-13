import { apiFetch } from "./client";
import { queryKeys } from "./query-keys";
import type { AuditActionsResponse, AuditLogListResponse } from "./types";

export async function apiListAuditLog(params?: {
  limit?: number;
  offset?: number;
  action?: string;
  actor_user_id?: string;
  start?: string;
  end?: string;
}): Promise<AuditLogListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));
  if (params?.offset !== undefined) searchParams.set("offset", String(params.offset));
  if (params?.action) searchParams.set("action", params.action);
  if (params?.actor_user_id) searchParams.set("actor_user_id", params.actor_user_id);
  if (params?.start) searchParams.set("start", params.start);
  if (params?.end) searchParams.set("end", params.end);
  const query = searchParams.toString() ? `?${searchParams}` : "";
  return apiFetch<AuditLogListResponse>(`/api/audit-log${query}`);
}

export async function apiGetAuditActions(): Promise<AuditActionsResponse> {
  return apiFetch<AuditActionsResponse>("/api/audit-log/actions");
}

export { queryKeys };
