import { apiFetch } from "./client";
import type {
  UserCreateRequest,
  UserCreateResponse,
  UserDetailResponse,
  UserListResponse,
  UserUpdate,
} from "./types";

export async function apiListUsers(): Promise<UserListResponse> {
  return apiFetch<UserListResponse>("/api/users");
}

export async function apiGetUser(userId: string): Promise<UserDetailResponse> {
  return apiFetch<UserDetailResponse>(`/api/users/${userId}`);
}

export async function apiCreateUser(
  body: UserCreateRequest,
): Promise<UserCreateResponse> {
  return apiFetch<UserCreateResponse>("/api/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateUser(
  userId: string,
  body: UserUpdate,
): Promise<UserDetailResponse> {
  return apiFetch<UserDetailResponse>(`/api/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export async function apiImpersonateUser(userId: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/users/${userId}/impersonate`, {
    method: "POST",
  });
}
