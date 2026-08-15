import { apiFetch } from "./client";
import { queryKeys } from "./query-keys";
import type { AnalyticsResponse } from "./types";

export async function apiGetAnalytics(params?: {
  start?: string;
  end?: string;
  model_limit?: number;
  model_sort?: string;
  user_limit?: number;
  user_sort?: string;
}): Promise<AnalyticsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.start) searchParams.set("start", params.start);
  if (params?.end) searchParams.set("end", params.end);
  if (params?.model_limit) searchParams.set("model_limit", String(params.model_limit));
  if (params?.model_sort) searchParams.set("model_sort", params.model_sort);
  if (params?.user_limit) searchParams.set("user_limit", String(params.user_limit));
  if (params?.user_sort) searchParams.set("user_sort", params.user_sort);
  const query = searchParams.toString() ? `?${searchParams}` : "";
  return apiFetch<AnalyticsResponse>(`/api/analytics${query}`);
}

export { queryKeys };
