import { useQuery } from "@tanstack/react-query";
import { apiListAvailableModels } from "../api/models";

export function useEnabledModels() {
  return useQuery({
    queryKey: ["models", "available"],
    queryFn: apiListAvailableModels,
    staleTime: 30_000,
  });
}
