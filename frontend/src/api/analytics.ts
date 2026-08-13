import { apiFetch } from "./client";
import { queryKeys } from "./query-keys";
import type { AnalyticsResponse } from "./types";

export async function apiGetAnalytics(params?: {
  start?: string;
  end?: string;
}): Promise<AnalyticsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.start) searchParams.set("start", params.start);
  if (params?.end) searchParams.set("end", params.end);
  const query = searchParams.toString() ? `?${searchParams}` : "";
  return apiFetch<AnalyticsResponse>(`/api/analytics${query}`);
}

export { queryKeys };
