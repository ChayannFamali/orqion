import { useQuery } from "@tanstack/react-query";
import { apiListAvailableModels } from "../api/models";
import { queryKeys } from "../api/query-keys";

export function useEnabledModels() {
  return useQuery({
    queryKey: queryKeys.models.available,
    queryFn: apiListAvailableModels,
    staleTime: 30_000,
  });
}
