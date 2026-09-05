import { apiFetch } from "./client";
import type {
  SkillAvailableListResponse,
  SkillCreate,
  SkillDeleteResponse,
  SkillListResponse,
  SkillResponse,
  SkillUpdate,
} from "./types";

/**
 * Т-508: скиллы — пакеты конфигурации агентного прогона.
 *
 * Два списка (решение 4): админский каталог `/api/skills` (способность
 * `manage_skills`, все скиллы включая выключенные) и список для выбора в
 * диалоге `/api/skills/available` (всем аутентифицированным, только
 * включённые, только нужные для выбора поля).
 */
export async function apiListSkills(): Promise<SkillListResponse> {
  return apiFetch<SkillListResponse>("/api/skills");
}

export async function apiListAvailableSkills(): Promise<SkillAvailableListResponse> {
  return apiFetch<SkillAvailableListResponse>("/api/skills/available");
}

export async function apiCreateSkill(body: SkillCreate): Promise<SkillResponse> {
  return apiFetch<SkillResponse>("/api/skills", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateSkill(
  skillId: string,
  body: SkillUpdate,
): Promise<SkillResponse> {
  return apiFetch<SkillResponse>(`/api/skills/${skillId}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function apiDeleteSkill(skillId: string): Promise<SkillDeleteResponse> {
  return apiFetch<SkillDeleteResponse>(`/api/skills/${skillId}`, {
    method: "DELETE",
  });
}
