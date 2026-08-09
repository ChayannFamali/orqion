import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  apiListConversations,
  apiGetConversation,
  apiCreateConversation,
  apiUpdateConversation,
  apiDeleteConversation,
} from "../api/conversations";

export function useConversations() {
  return useQuery({
    queryKey: ["conversations"],
    queryFn: () => apiListConversations(),
    staleTime: 10_000,
  });
}

export function useConversation(id: string | null) {
  return useQuery({
    queryKey: ["conversations", id],
    queryFn: () => apiGetConversation(id!),
    enabled: id !== null,
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title: string | null) => apiCreateConversation(title),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useUpdateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title, archived }: { id: string; title?: string; archived?: boolean }) =>
      apiUpdateConversation(id, { title, archived }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiDeleteConversation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}
