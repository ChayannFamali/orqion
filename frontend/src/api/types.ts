/**
 * Re-export типов API.
 *
 * Сгенерированные типы — из generated.ts (T-301, openapi-typescript).
 * Клиентские типы (ApiError, SSEEvent) — из runtime.ts, не описаны в OpenAPI.
 *
 * Этот файл — точка импорта для всех потребителей. При изменении схемы
 * типы обновляются автоматически через `npm run gen:types`.
 */

export type { ApiError, SSEEvent, SourceEntry, ChatCompletionResult } from "./runtime";

import type { components } from "./generated";

export type LoginRequest = components["schemas"]["LoginRequest"];
export type LoginResponse = components["schemas"]["LoginResponse"];
export type UserResponse = components["schemas"]["UserResponse"];
export type MessageResponse = components["schemas"]["MessageResponse"];
export type ConversationResponse = components["schemas"]["ConversationResponse"];
export type ConversationDetailResponse = components["schemas"]["ConversationDetailResponse"];
export type ConversationListResponse = components["schemas"]["ConversationListResponse"];
export type ChatMessage = components["schemas"]["ChatMessage"];
export type ChatRequest = components["schemas"]["ChatRequest"];
export type ModelInfo = components["schemas"]["ModelResponse"];
export type ProviderInfo = components["schemas"]["ProviderResponse"];
export type ProviderListResponse = components["schemas"]["ProviderListResponse"];
