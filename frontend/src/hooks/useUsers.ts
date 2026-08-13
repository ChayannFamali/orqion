import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiImpersonateUser, apiListUsers, apiUpdateUser } from "../api/users";
import { apiExitImpersonation } from "../api/auth";
import { queryKeys } from "../api/query-keys";
import type { UserUpdate } from "../api/types";

export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users.all,
    queryFn: apiListUsers,
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, body }: { userId: string; body: UserUpdate }) =>
      apiUpdateUser(userId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
    },
  });
}

export function useImpersonateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) => apiImpersonateUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.me });
    },
  });
}

export function useExitImpersonation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiExitImpersonation(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.me });
    },
  });
}
