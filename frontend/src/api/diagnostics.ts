import { apiFetch } from "./client";
import type { EnvironmentDiagnosticsResponse } from "./types";

/** T-444: снимок окружения хоста (только чтение). */
export async function apiGetEnvironmentDiagnostics(): Promise<EnvironmentDiagnosticsResponse> {
  return apiFetch<EnvironmentDiagnosticsResponse>("/api/diagnostics/environment");
}
