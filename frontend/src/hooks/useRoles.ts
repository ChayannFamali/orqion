import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCreateRole, apiListRoles, apiUpdateRole } from "../api/roles";
import { queryKeys } from "../api/query-keys";
import type { RoleCreate, RoleUpdate } from "../api/types";

export function useRoles() {
  return useQuery({
    queryKey: queryKeys.roles.all,
    queryFn: apiListRoles,
  });
}

export function useCreateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RoleCreate) => apiCreateRole(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.roles.all });
    },
  });
}

export function useUpdateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ roleId, body }: { roleId: string; body: RoleUpdate }) =>
      apiUpdateRole(roleId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.roles.all });
    },
  });
}
