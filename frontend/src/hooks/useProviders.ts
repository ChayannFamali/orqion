import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCreateModel, apiCreateProvider, apiListProviders, apiProbeProvider, apiUpdateModel, apiUpdateProvider } from "../api/providers";
import { queryKeys } from "../api/query-keys";
import type { ModelCreate, ModelUpdate, ProviderCreate, ProviderUpdate } from "../api/types";

export function useProviders() {
  return useQuery({
    queryKey: queryKeys.providers.all,
    queryFn: apiListProviders,
  });
}

export function useCreateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProviderCreate) => apiCreateProvider(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useUpdateProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, body }: { providerId: string; body: ProviderUpdate }) =>
      apiUpdateProvider(providerId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useProbeProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, deep = false }: { providerId: string; deep?: boolean }) =>
      apiProbeProvider(providerId, deep),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useCreateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ providerId, body }: { providerId: string; body: ModelCreate }) =>
      apiCreateModel(providerId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}

export function useUpdateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, body }: { modelId: string; body: ModelUpdate }) =>
      apiUpdateModel(modelId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.providers.all });
    },
  });
}
