import type { ApiError } from "./runtime";

/**
 * Разбирает ошибку из HTTP-ответа.
 *
 * Бэкенд возвращает { error, reason, constraint, hint } для OrqionError
 * (см. backend/app/api/exception_handlers.py). Если JSON неразборчив или
 * тело пустое — возвращает HTTP-статус как причину.
 */
export async function parseError(response: Response): Promise<ApiError> {
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
 * Единая обёртка над fetch для JSON API.
 *
 * - Добавляет Content-Type: application/json для запросов с телом
 * - Парсит ошибки через parseError → ApiError
 * - Возвращает типизированный JSON-ответ
 *
 * Для не-JSON запросов (SSE, FormData) используйте fetch напрямую.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(path, { ...init, headers, credentials: "include" });

  if (!res.ok) {
    throw await parseError(res);
  }

  // 204 No Content или пустой body
  if (res.status === 204 || res.headers.get("content-length") === "0") {
    return undefined as T;
  }

  return (await res.json()) as T;
}

export interface HealthResponse {
  status: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}
