import type { ApiError, ModelInfo } from "./types";

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

/**
 * GET /api/models — модели, доступные текущему пользователю.
 *
 * Бэкенд фильтрует по policy.models + enabled=True.
 * Возвращает плоский список, не сгруппированный по провайдерам.
 */
export async function apiListAvailableModels(): Promise<ModelInfo[]> {
  const res = await fetch("/api/models");
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as ModelInfo[];
}
