import { useQuery } from "@tanstack/react-query";
import { apiGetTrace, apiListTraces } from "../api/traces";
import { queryKeys } from "../api/query-keys";

export function useTraces(conversationId?: string) {
  return useQuery({
    queryKey: conversationId
      ? [...queryKeys.traces.all, { conversation_id: conversationId }]
      : queryKeys.traces.all,
    queryFn: () => apiListTraces({ conversation_id: conversationId }),
  });
}

export function useTraceDetail(traceId: string | null) {
  return useQuery({
    queryKey: queryKeys.traces.detail(traceId ?? ""),
    queryFn: () => apiGetTrace(traceId!),
    enabled: traceId !== null,
  });
}
