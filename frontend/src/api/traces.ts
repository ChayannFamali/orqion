import { apiFetch } from "./client";
import { queryKeys } from "./query-keys";
import type { TraceDetailResponse, TraceListResponse } from "./types";

export async function apiListTraces(params?: {
  conversation_id?: string;
  limit?: number;
  offset?: number;
}): Promise<TraceListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.conversation_id) searchParams.set("conversation_id", params.conversation_id);
  if (params?.limit) searchParams.set("limit", String(params.limit));
  if (params?.offset) searchParams.set("offset", String(params.offset));
  const query = searchParams.toString() ? `?${searchParams}` : "";
  return apiFetch<TraceListResponse>(`/api/traces${query}`);
}

export async function apiGetTrace(traceId: string): Promise<TraceDetailResponse> {
  return apiFetch<TraceDetailResponse>(`/api/traces/${traceId}`);
}

export { queryKeys };
