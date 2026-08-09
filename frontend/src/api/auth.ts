import type { ApiError, LoginRequest, LoginResponse, UserResponse } from "./types";

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError>;
    return {
      error: body.error ?? "unknown",
      reason: body.reason ?? "Неизвестная ошибка",
      constraint: body.constraint ?? null,
      hint: body.hint ?? null,
    };
  } catch {
    return {
      error: "http_error",
      reason: `HTTP ${response.status}`,
      constraint: null,
      hint: null,
    };
  }
}

export async function apiLogin(body: LoginRequest): Promise<LoginResponse> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as LoginResponse;
}

export async function apiLogout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
}

export async function apiGetMe(): Promise<UserResponse> {
  const res = await fetch("/api/auth/me");
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as UserResponse;
}
