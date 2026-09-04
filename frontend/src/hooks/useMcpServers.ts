import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCreateMcpServer,
  apiDeleteMcpServer,
  apiListMcpServers,
  apiUpdateMcpServer,
} from "../api/mcpServers";
import { queryKeys } from "../api/query-keys";
import type { McpServerCreate, McpServerUpdate } from "../api/types";

/** Т-503: реестр серверов внешних инструментов (админский раздел). */
export function useMcpServers() {
  return useQuery({
    queryKey: queryKeys.mcpServers.all,
    queryFn: apiListMcpServers,
  });
}

export function useCreateMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: McpServerCreate) => apiCreateMcpServer(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers.all });
    },
  });
}

export function useUpdateMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ serverId, body }: { serverId: string; body: McpServerUpdate }) =>
      apiUpdateMcpServer(serverId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers.all });
    },
  });
}

export function useDeleteMcpServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: string) => apiDeleteMcpServer(serverId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.mcpServers.all });
    },
  });
}
