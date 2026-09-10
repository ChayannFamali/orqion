import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../api/query-keys";
import {
  apiGetWorkspaceSettings,
  apiUpdateWorkspaceSetting,
} from "../api/workspaceSettings";
import type { WorkspaceSettingUpdate } from "../api/types";

/**
 * Реестр служебных настроек рабочей области.
 *
 * Запись одного ключа инвалидирует весь список: источник значения
 * (`default` → `db`) и соседние ключи приходят одним ответом, а частичное
 * обновление кэша разошлось бы с сервером.
 */
export function useWorkspaceSettings() {
  return useQuery({
    queryKey: queryKeys.workspaceSettings.all,
    queryFn: apiGetWorkspaceSettings,
  });
}

export function useUpdateWorkspaceSetting() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkspaceSettingUpdate) => apiUpdateWorkspaceSetting(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaceSettings.all });
    },
  });
}
