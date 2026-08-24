import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCreateCorpus,
  apiListAvailableCorpora,
  apiListCorpora,
  apiUpdateCorpus,
} from "../api/corpora";
import { queryKeys } from "../api/query-keys";
import type { CorpusCreate, CorpusUpdate } from "../api/types";

export function useCorpora() {
  return useQuery({
    queryKey: queryKeys.corpora.all,
    queryFn: apiListCorpora,
  });
}

/** T-439: корпуса, доступные пользователю для чата (селектор в чате). */
export function useAvailableCorpora() {
  return useQuery({
    queryKey: queryKeys.corpora.available,
    queryFn: apiListAvailableCorpora,
  });
}

export function useCreateCorpus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CorpusCreate) => apiCreateCorpus(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.corpora.all });
    },
  });
}

export function useUpdateCorpus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CorpusUpdate }) =>
      apiUpdateCorpus(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.corpora.all });
    },
  });
}
