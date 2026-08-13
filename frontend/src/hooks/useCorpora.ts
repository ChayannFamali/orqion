import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiCreateCorpus, apiListCorpora } from "../api/corpora";
import { queryKeys } from "../api/query-keys";
import type { CorpusCreate } from "../api/types";

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
