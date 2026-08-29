import { apiFetch } from "./client";
import type {
  PromptTemplateCreate,
  PromptTemplateListResponse,
  PromptTemplateResponse,
  PromptTemplateUpdate,
} from "./types";

/** Т-507: список личных сохранённых промптов пользователя. */
export async function apiListPromptTemplates(): Promise<PromptTemplateListResponse> {
  return apiFetch<PromptTemplateListResponse>("/api/prompt-templates");
}

/** Т-507: создание шаблона промпта. */
export async function apiCreatePromptTemplate(
  body: PromptTemplateCreate,
): Promise<PromptTemplateResponse> {
  return apiFetch<PromptTemplateResponse>("/api/prompt-templates", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Т-507: изменение шаблона промпта (только владельцем). */
export async function apiUpdatePromptTemplate(
  id: string,
  body: PromptTemplateUpdate,
): Promise<PromptTemplateResponse> {
  return apiFetch<PromptTemplateResponse>(`/api/prompt-templates/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** Т-507: удаление шаблона промпта (только владельцем). */
export async function apiDeletePromptTemplate(id: string): Promise<void> {
  await apiFetch<void>(`/api/prompt-templates/${id}`, { method: "DELETE" });
}
