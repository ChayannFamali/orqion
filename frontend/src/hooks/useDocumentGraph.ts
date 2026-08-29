import { useQuery } from "@tanstack/react-query";
import { apiGetDocumentGraph } from "../api/documentGraph";
import { queryKeys } from "../api/query-keys";

/** Т-505: граф связей документов выбранного корпуса. */
export function useDocumentGraph(corpusId: string | null) {
  return useQuery({
    queryKey: queryKeys.documentGraph.byCorpus(corpusId ?? ""),
    queryFn: () => apiGetDocumentGraph(corpusId as string),
    enabled: corpusId !== null,
  });
}
