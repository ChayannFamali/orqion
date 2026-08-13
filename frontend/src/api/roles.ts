import { apiFetch } from "./client";
import type { RoleCreate, RoleListResponse, RoleResponse, RoleUpdate } from "./types";

export async function apiListRoles(): Promise<RoleListResponse> {
  return apiFetch<RoleListResponse>("/api/roles");
}

export async function apiGetRole(roleId: string): Promise<RoleResponse> {
  return apiFetch<RoleResponse>(`/api/roles/${roleId}`);
}

export async function apiCreateRole(body: RoleCreate): Promise<RoleResponse> {
  return apiFetch<RoleResponse>("/api/roles", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateRole(
  roleId: string,
  body: RoleUpdate,
): Promise<RoleResponse> {
  return apiFetch<RoleResponse>(`/api/roles/${roleId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
