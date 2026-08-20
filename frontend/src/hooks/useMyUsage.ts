import { useQuery } from "@tanstack/react-query";
import { apiGetMyUsage } from "../api/me";
import { queryKeys } from "../api/query-keys";

export function useMyUsage() {
  return useQuery({
    queryKey: queryKeys.auth.usage,
    queryFn: apiGetMyUsage,
    refetchInterval: 60_000, // 60s — расход не меняется быстро
    staleTime: 30_000,
  });
}
