import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "../api/client";
import { queryKeys } from "../api/query-keys";

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: fetchHealth,
    staleTime: 5000,
  });
}
