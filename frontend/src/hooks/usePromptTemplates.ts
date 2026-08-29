import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCreatePromptTemplate,
  apiDeletePromptTemplate,
  apiListPromptTemplates,
  apiUpdatePromptTemplate,
} from "../api/promptTemplates";
import { queryKeys } from "../api/query-keys";
import type { PromptTemplateCreate, PromptTemplateUpdate } from "../api/types";

/** Т-507: личные сохранённые промпты текущего пользователя. */
export function usePromptTemplates(enabled: boolean = true) {
  return useQuery({
    queryKey: queryKeys.promptTemplates.all,
    queryFn: apiListPromptTemplates,
    enabled,
  });
}

export function useCreatePromptTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: PromptTemplateCreate) => apiCreatePromptTemplate(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.promptTemplates.all });
    },
  });
}

export function useUpdatePromptTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: PromptTemplateUpdate }) =>
      apiUpdatePromptTemplate(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.promptTemplates.all });
    },
  });
}

export function useDeletePromptTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDeletePromptTemplate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.promptTemplates.all });
    },
  });
}
