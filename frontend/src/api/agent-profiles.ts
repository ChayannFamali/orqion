import { apiFetch } from "./client";
import type {
  AgentProfileAvailableListResponse,
  AgentProfileConversationListResponse,
  AgentProfileCreate,
  AgentProfileDeleteResponse,
  AgentProfileListResponse,
  AgentProfileResponse,
  AgentProfileUpdate,
} from "./types";

/**
 * Т-509: профили агентов — переиспользуемые конфигурации диалога.
 *
 * Профиль — модель + скилл (решение 1): отдельного системного промпта и
 * списка инструментов в профиле нет, эту роль выполняет скилл (Т-508).
 *
 * Два списка по образцу разделения Т-508: админский каталог
 * `/api/agent-profiles` (способность `manage_agents`, все профили включая
 * отключённые) и список для старта диалога `/api/agent-profiles/available`
 * (всем аутентифицированным, только включённые, только поля выбора).
 */
export async function apiListAgentProfiles(): Promise<AgentProfileListResponse> {
  return apiFetch<AgentProfileListResponse>("/api/agent-profiles");
}

export async function apiListAvailableAgentProfiles(): Promise<AgentProfileAvailableListResponse> {
  return apiFetch<AgentProfileAvailableListResponse>("/api/agent-profiles/available");
}

export async function apiCreateAgentProfile(
  body: AgentProfileCreate,
): Promise<AgentProfileResponse> {
  return apiFetch<AgentProfileResponse>("/api/agent-profiles", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateAgentProfile(
  profileId: string,
  body: AgentProfileUpdate,
): Promise<AgentProfileResponse> {
  return apiFetch<AgentProfileResponse>(`/api/agent-profiles/${profileId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function apiDeleteAgentProfile(
  profileId: string,
): Promise<AgentProfileDeleteResponse> {
  return apiFetch<AgentProfileDeleteResponse>(`/api/agent-profiles/${profileId}`, {
    method: "DELETE",
  });
}

/**
 * Drill-down по диалогам профиля (решение 5): метаданные и расход, без
 * содержимого переписки. Без `manage_agents` сервер возвращает только
 * собственные диалоги пользователя (поле `scope` в ответе — "own"),
 * с правом — все диалоги рабочей области ("workspace"), но теми же
 * полями метаданных.
 */
export async function apiListAgentProfileConversations(
  profileId: string,
  limit = 50,
  offset = 0,
): Promise<AgentProfileConversationListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<AgentProfileConversationListResponse>(
    `/api/agent-profiles/${profileId}/conversations?${params.toString()}`,
  );
}
