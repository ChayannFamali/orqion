import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  apiListConversations,
  apiGetConversation,
  apiCreateConversation,
  apiUpdateConversation,
  apiDeleteConversation,
  apiResetConversationContext,
} from "../api/conversations";
import { queryKeys } from "../api/query-keys";

export function useConversations() {
  return useQuery({
    queryKey: queryKeys.conversations.all,
    queryFn: () => apiListConversations(),
    staleTime: 10_000,
    // T-433: refetch каждые 5s если есть диалоги без заголовка (пустой title)
    // — fire-and-forget генерация заголовка обновит title в фоне. Прецедент:
    // useDocuments.ts refetchInterval для pending/indexing статусов (T-313).
    refetchInterval: (query) => {
      const convs = query.state.data?.conversations ?? [];
      const hasUntitled = convs.some((c: { title: string }) => c.title === "");
      return hasUntitled ? 5000 : false;
    },
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: queryKeys.conversations.detail(id ?? ""),
    queryFn: () => apiGetConversation(id!),
    enabled: id !== null,
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title: string | null) => apiCreateConversation(title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

export function useUpdateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title, archived }: { id: string; title?: string; archived?: boolean }) =>
      apiUpdateConversation(id, { title, archived }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDeleteConversation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}

/** T-442: мягкий сброс контекста диалога. */
export function useResetConversationContext() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiResetConversationContext(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.detail(id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.conversations.all });
    },
  });
}
