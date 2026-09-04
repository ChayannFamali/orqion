import { apiFetch } from "./client";
import type {
  McpServerCreate,
  McpServerDeleteResponse,
  McpServerListResponse,
  McpServerResponse,
  McpServerUpdate,
} from "./types";

/** Т-503: реестр серверов внешних инструментов (админский раздел). */
export async function apiListMcpServers(): Promise<McpServerListResponse> {
  return apiFetch<McpServerListResponse>("/api/mcp-servers");
}

export async function apiCreateMcpServer(
  body: McpServerCreate,
): Promise<McpServerResponse> {
  return apiFetch<McpServerResponse>("/api/mcp-servers", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateMcpServer(
  serverId: string,
  body: McpServerUpdate,
): Promise<McpServerResponse> {
  return apiFetch<McpServerResponse>(`/api/mcp-servers/${serverId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function apiDeleteMcpServer(
  serverId: string,
): Promise<McpServerDeleteResponse> {
  return apiFetch<McpServerDeleteResponse>(`/api/mcp-servers/${serverId}`, {
    method: "DELETE",
  });
}
