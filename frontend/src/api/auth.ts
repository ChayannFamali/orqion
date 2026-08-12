import { apiFetch } from "./client";
import type { LoginRequest, LoginResponse, UserResponse } from "./types";

export async function apiLogin(body: LoginRequest): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiLogout(): Promise<void> {
  await apiFetch<void>("/api/auth/logout", { method: "POST" });
}

export async function apiGetMe(): Promise<UserResponse> {
  return apiFetch<UserResponse>("/api/auth/me");
}
