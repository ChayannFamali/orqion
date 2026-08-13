import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDeleteDocument, apiListDocuments, apiUploadDocument } from "../api/documents";
import type { UploadProgress } from "../api/documents";
import { queryKeys } from "../api/query-keys";

export function useDocuments(corpusId: string | null) {
  return useQuery({
    queryKey: corpusId ? queryKeys.documents.byCorpus(corpusId) : ["documents", "none"],
    queryFn: () => apiListDocuments(corpusId!),
    enabled: !!corpusId,
    refetchInterval: (query) => {
      const docs = query.state.data?.documents ?? [];
      const hasPending = docs.some(
        (d) => d.status === "pending" || d.status === "indexing",
      );
      return hasPending ? 3000 : false;
    },
  });
}

export function useUploadDocument(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      file,
      onProgress,
    }: {
      file: File;
      onProgress?: (progress: UploadProgress) => void;
    }) => apiUploadDocument(corpusId, file, onProgress),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.byCorpus(corpusId) });
    },
  });
}

export function useDeleteDocument(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) => apiDeleteDocument(documentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.documents.byCorpus(corpusId) });
    },
  });
}
