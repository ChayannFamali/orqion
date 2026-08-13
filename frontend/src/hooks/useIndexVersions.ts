import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiActivateIndexVersion,
  apiBuildIndexVersion,
  apiCleanupRetiredVersions,
  apiListIndexVersions,
  apiRollbackIndexVersion,
} from "../api/indexVersions";
import { queryKeys } from "../api/query-keys";

export function useIndexVersions(corpusId: string | null) {
  return useQuery({
    queryKey: corpusId
      ? queryKeys.indexVersions.byCorpus(corpusId)
      : ["index-versions", "none"],
    queryFn: () => apiListIndexVersions(corpusId!),
    enabled: !!corpusId,
    refetchInterval: (query) => {
      const versions = query.state.data?.versions ?? [];
      const hasBuilding = versions.some((v) => v.status === "building");
      return hasBuilding ? 3000 : false;
    },
  });
}

export function useBuildIndexVersion(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiBuildIndexVersion(corpusId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.indexVersions.byCorpus(corpusId),
      });
    },
  });
}

export function useActivateIndexVersion(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) => apiActivateIndexVersion(corpusId, versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.indexVersions.byCorpus(corpusId),
      });
    },
  });
}

export function useRollbackIndexVersion(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiRollbackIndexVersion(corpusId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.indexVersions.byCorpus(corpusId),
      });
    },
  });
}

export function useCleanupRetiredVersions(corpusId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCleanupRetiredVersions(corpusId),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.indexVersions.byCorpus(corpusId),
      });
    },
  });
}
