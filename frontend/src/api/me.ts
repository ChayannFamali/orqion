import { apiFetch } from "./client";
import type { ApiError } from "./runtime";

export interface ModelUsageBreakdown {
  model_id: string;
  requests: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
}

export interface MyUsageResponse {
  tokens_used: number;
  tokens_limit: number | null;
  cost_used: number;
  cost_limit: number | null;
  period: string;
  by_model: ModelUsageBreakdown[];
  near_limit: boolean;
}

export async function apiGetMyUsage(): Promise<MyUsageResponse> {
  return apiFetch<MyUsageResponse>("/api/auth/me/usage");
}

export type { ApiError };
