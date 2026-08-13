import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCreateCorpus, apiListCorpora, apiUpdateCorpus } from "../api/corpora";
import { queryKeys } from "../api/query-keys";
import type { CorpusCreate, CorpusUpdate } from "../api/types";

export function useCorpora() {
  return useQuery({
    queryKey: queryKeys.corpora.all,
    queryFn: apiListCorpora,
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
