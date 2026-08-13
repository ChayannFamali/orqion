import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCompareEvalRuns,
  apiCreateEvalItem,
  apiCreateEvalRun,
  apiCreateEvalSet,
  apiDeleteEvalItem,
  apiDeleteEvalSet,
  apiGetEvalSet,
  apiListEvalRuns,
  apiListEvalSets,
} from "../api/eval";
import { queryKeys } from "../api/query-keys";

export function useEvalSets(corpusId: string) {
  return useQuery({
    queryKey: queryKeys.evalSets.byCorpus(corpusId),
    queryFn: () => apiListEvalSets(corpusId),
  });
}

export function useEvalSet(evalSetId: string | null) {
  return useQuery({
    queryKey: evalSetId
      ? queryKeys.evalSets.detail(evalSetId)
      : ["eval-sets", "none"],
    queryFn: () => apiGetEvalSet(evalSetId!),
    enabled: !!evalSetId,
  });
}

export function useCreateEvalSet(corpusId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: apiCreateEvalSet.bind(null, corpusId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.evalSets.byCorpus(corpusId) });
    },
  });
}

export function useDeleteEvalSet(corpusId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (evalSetId: string) => apiDeleteEvalSet(evalSetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.evalSets.byCorpus(corpusId) });
    },
  });
}

export function useCreateEvalItem(evalSetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: apiCreateEvalItem.bind(null, evalSetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.evalSets.detail(evalSetId) });
    },
  });
}

export function useDeleteEvalItem(evalSetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => apiDeleteEvalItem(evalSetId, itemId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.evalSets.detail(evalSetId) });
    },
  });
}

export function useEvalRuns(evalSetId: string | null) {
  return useQuery({
    queryKey: evalSetId
      ? queryKeys.evalRuns.bySet(evalSetId)
      : ["eval-runs", "none"],
    queryFn: () => apiListEvalRuns(evalSetId!),
    enabled: !!evalSetId,
  });
}

export function useCreateEvalRun(evalSetId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: apiCreateEvalRun.bind(null, evalSetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.evalRuns.bySet(evalSetId) });
    },
  });
}

export function useCompareEvalRuns() {
  return useMutation({
    mutationFn: apiCompareEvalRuns,
  });
}
