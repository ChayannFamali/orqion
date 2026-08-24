import { useQuery } from "@tanstack/react-query";
import { apiGetEnvironmentDiagnostics } from "../api/diagnostics";
import { queryKeys } from "../api/query-keys";

/** T-444: диагностика окружения — снапшот по открытию раздела, без поллинга. */
export function useEnvironmentDiagnostics() {
  return useQuery({
    queryKey: queryKeys.diagnostics.environment,
    queryFn: apiGetEnvironmentDiagnostics,
  });
}
