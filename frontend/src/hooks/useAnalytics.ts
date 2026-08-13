import { useQuery } from "@tanstack/react-query";
import { apiGetAnalytics } from "../api/analytics";
import { queryKeys } from "../api/query-keys";

export function useAnalytics(params?: { start?: string; end?: string }) {
  const start = params?.start ?? "default";
  const end = params?.end ?? "default";
  return useQuery({
    queryKey: queryKeys.analytics.range(start, end),
    queryFn: () => apiGetAnalytics(params),
  });
}
