import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGetMe, apiLogin, apiLogout } from "../api/auth";
import { queryKeys } from "../api/query-keys";
import type { LoginRequest } from "../api/types";

export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.auth.me,
    queryFn: apiGetMe,
    retry: false,
    staleTime: 300_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginRequest) => apiLogin(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.me });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: apiLogout,
    onSuccess: () => {
      queryClient.clear();
    },
  });
}
