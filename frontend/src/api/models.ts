import { apiFetch } from "./client";
import type { ModelInfo } from "./types";

/**
 * GET /api/models — модели, доступные текущему пользователю.
 *
 * Бэкенд фильтрует по policy.models + enabled=True.
 * Возвращает плоский список, не сгруппированный по провайдерам.
 */
export async function apiListAvailableModels(): Promise<ModelInfo[]> {
  return apiFetch<ModelInfo[]>("/api/models");
}
