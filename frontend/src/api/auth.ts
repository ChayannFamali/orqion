import { apiFetch } from "./client";
import type {
  ChangePasswordRequest,
  ChangePasswordResponse,
  LoginRequest,
  LoginResponse,
  UserResponse,
} from "./types";

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

export async function apiChangePassword(
  body: ChangePasswordRequest,
): Promise<ChangePasswordResponse> {
  return apiFetch<ChangePasswordResponse>("/api/auth/change-password", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiExitImpersonation(): Promise<{ status: string; reason?: string }> {
  return apiFetch<{ status: string; reason?: string }>("/api/auth/exit-impersonation", {
    method: "POST",
  });
}
