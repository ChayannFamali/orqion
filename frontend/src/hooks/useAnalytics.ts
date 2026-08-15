import { useQuery } from "@tanstack/react-query";
import { apiGetAnalytics } from "../api/analytics";

export function useAnalytics(params?: {
  start?: string;
  end?: string;
  model_limit?: number;
  model_sort?: string;
  user_limit?: number;
  user_sort?: string;
}) {
  const start = params?.start ?? "default";
  const end = params?.end ?? "default";
  const ml = params?.model_limit ?? "all";
  const ms = params?.model_sort ?? "requests";
  const ul = params?.user_limit ?? "all";
  const us = params?.user_sort ?? "requests";
  return useQuery({
    queryKey: ["analytics", start, end, ml, ms, ul, us],
    queryFn: () => apiGetAnalytics(params),
  });
}
