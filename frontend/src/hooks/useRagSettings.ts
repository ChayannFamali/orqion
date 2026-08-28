import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGetRagSettings, apiUpdateRagSettings } from "../api/ragSettings";
import { queryKeys } from "../api/query-keys";
import type { RagSettingsUpdate } from "../api/types";

/** Т-506: настройки RAG-поиска уровня рабочей области. */
export function useRagSettings() {
  return useQuery({
    queryKey: queryKeys.ragSettings.all,
    queryFn: apiGetRagSettings,
  });
}

export function useUpdateRagSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RagSettingsUpdate) => apiUpdateRagSettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.ragSettings.all });
    },
  });
}
