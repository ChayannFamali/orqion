import { apiFetch } from "./client";
import type { AgentChatRequest, AgentChatResponse } from "./types";

/**
 * Агентный прогон (Т-502): цикл «модель → инструменты → модель».
 *
 * Синхронный запрос — ответ приходит одним JSON (стриминга нет, решение 2
 * дизайн-ревью). Тело содержит шаги прогона, источники поиска и, при
 * остановке на деструктивном инструменте, запрос подтверждения.
 *
 * Честная деградация: без дополнения ``orqion[agent]`` бэкенд отвечает
 * 200 с ``available=false`` и причиной — не ошибкой.
 */
export async function agentChat(
  body: AgentChatRequest,
  signal?: AbortSignal,
): Promise<AgentChatResponse> {
  return apiFetch<AgentChatResponse>("/api/agent/chat", {
    method: "POST",
    body: JSON.stringify(body),
    signal,
  });
}
