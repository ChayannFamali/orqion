import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiCreateSkill,
  apiDeleteSkill,
  apiListAvailableSkills,
  apiListSkills,
  apiUpdateSkill,
} from "../api/skills";
import { queryKeys } from "../api/query-keys";
import type { SkillCreate, SkillUpdate } from "../api/types";

/** Т-508: админский каталог скиллов (способность manage_skills). */
export function useSkills() {
  return useQuery({
    queryKey: queryKeys.skills.all,
    queryFn: apiListSkills,
  });
}

/**
 * Т-508: скиллы, доступные текущему пользователю для выбора в диалоге.
 *
 * Список нужен только в агентном режиме, поэтому запрос условный
 * (паттерн ``usePromptTemplates(canPrompts)`` из Т-507).
 */
export function useAvailableSkills(enabled = true) {
  return useQuery({
    queryKey: queryKeys.skills.available,
    queryFn: apiListAvailableSkills,
    enabled,
  });
}

export function useCreateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SkillCreate) => apiCreateSkill(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.available });
    },
  });
}

export function useUpdateSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ skillId, body }: { skillId: string; body: SkillUpdate }) =>
      apiUpdateSkill(skillId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.available });
    },
  });
}

export function useDeleteSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (skillId: string) => apiDeleteSkill(skillId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.skills.available });
    },
  });
}
