import { useQuery } from "@tanstack/react-query";
import { apiGetAuditActions, apiListAuditLog } from "../api/audit";
import { queryKeys } from "../api/query-keys";

export function useAuditLog(params?: {
  limit?: number;
  offset?: number;
  action?: string;
  actor_user_id?: string;
  start?: string;
  end?: string;
}) {
  return useQuery({
    queryKey: [...queryKeys.audit.all, params],
    queryFn: () => apiListAuditLog(params),
  });
}

export function useAuditActions() {
  return useQuery({
    queryKey: queryKeys.audit.actions,
    queryFn: () => apiGetAuditActions(),
  });
}
