import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCreateAgentProfile,
  apiDeleteAgentProfile,
  apiListAgentProfileConversations,
  apiListAgentProfiles,
  apiListAvailableAgentProfiles,
  apiUpdateAgentProfile,
} from "../api/agent-profiles";
import { queryKeys } from "../api/query-keys";
import type { AgentProfileCreate, AgentProfileUpdate } from "../api/types";

/**
 * Т-509: админский каталог профилей (способность `manage_agents`).
 * Запрос условный: без права каталога не существует (404), поэтому
 * `enabled` передаёт наличие права (паттерн `useSkills`/`usePromptTemplates`).
 */
export function useAgentProfiles(enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentProfiles.all,
    queryFn: apiListAgentProfiles,
    enabled,
  });
}

/**
 * Т-509: профили, доступные текущему пользователю для старта диалога.
 * Всем аутентифицированным, только включённые, только поля выбора.
 */
export function useAvailableAgentProfiles(enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentProfiles.available,
    queryFn: apiListAvailableAgentProfiles,
    enabled,
  });
}

/**
 * Т-509 (решение 5): диалоги профиля — метаданные и расход, без переписки.
 * Охват (свои/все) сервер выбирает сам по праву; поле `scope` в ответе
 * делает фактический охват видимым.
 */
export function useAgentProfileConversations(profileId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.agentProfiles.conversations(profileId ?? ""),
    queryFn: () => apiListAgentProfileConversations(profileId!),
    enabled: enabled && profileId !== null,
  });
}

function useInvalidateProfiles() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.agentProfiles.all });
    queryClient.invalidateQueries({ queryKey: queryKeys.agentProfiles.available });
  };
}

export function useCreateAgentProfile() {
  const invalidate = useInvalidateProfiles();
  return useMutation({
    mutationFn: (body: AgentProfileCreate) => apiCreateAgentProfile(body),
    onSuccess: invalidate,
  });
}

export function useUpdateAgentProfile() {
  const invalidate = useInvalidateProfiles();
  return useMutation({
    mutationFn: ({ profileId, body }: { profileId: string; body: AgentProfileUpdate }) =>
      apiUpdateAgentProfile(profileId, body),
    onSuccess: invalidate,
  });
}

export function useDeleteAgentProfile() {
  const invalidate = useInvalidateProfiles();
  return useMutation({
    mutationFn: (profileId: string) => apiDeleteAgentProfile(profileId),
    onSuccess: invalidate,
  });
}
