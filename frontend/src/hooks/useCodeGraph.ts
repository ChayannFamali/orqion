import { useQuery } from "@tanstack/react-query";
import { apiGetCodeGraph } from "../api/codeGraph";
import { queryKeys } from "../api/query-keys";

/** Т-504: граф связей кода выбранного корпуса. */
export function useCodeGraph(corpusId: string | null) {
  return useQuery({
    queryKey: queryKeys.codeGraph.byCorpus(corpusId ?? ""),
    queryFn: () => apiGetCodeGraph(corpusId as string),
    enabled: corpusId !== null,
  });
}
