/**
 * Re-export типов API.
 *
 * Сгенерированные типы — из generated.ts (T-301, openapi-typescript).
 * Клиентские типы (ApiError, SSEEvent) — из runtime.ts, не описаны в OpenAPI.
 *
 * Этот файл — точка импорта для всех потребителей. При изменении схемы
 * типы обновляются автоматически через `npm run gen:types`.
 */

export type { ApiError, SSEEvent, SourceEntry, ChatCompletionResult, ModelStatus, ProbeResult, RoleResponse, RoleListResponse, RoleCreate, RoleUpdate, UserListItem, UserListResponse, UserDetailResponse, UserUpdate, CorpusResponse, CorpusListResponse, CorpusCreate } from "./runtime";

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
export type ModelResponse = components["schemas"]["ModelResponse"];
export type ProviderInfo = components["schemas"]["ProviderResponse"];
export type ProviderResponse = components["schemas"]["ProviderResponse"];
export type ProviderListResponse = components["schemas"]["ProviderListResponse"];
export type ProviderCreate = components["schemas"]["ProviderCreate"];
export type ProviderUpdate = components["schemas"]["ProviderUpdate"];
export type ModelCreate = components["schemas"]["ModelCreate"];
export type ModelUpdate = components["schemas"]["ModelUpdate"];
export type SpanResponse = components["schemas"]["SpanResponse"];
export type TraceSummaryResponse = components["schemas"]["TraceSummaryResponse"];
export type TraceListResponse = components["schemas"]["TraceListResponse"];
export type TraceDetailResponse = components["schemas"]["TraceDetailResponse"];
export type DocumentResponse = components["schemas"]["DocumentResponse"];
export type DocumentDetailResponse = components["schemas"]["DocumentDetailResponse"];
export type DocumentListResponse = components["schemas"]["DocumentListResponse"];
export type IndexVersionResponse = components["schemas"]["IndexVersionResponse"];
export type IndexVersionListResponse = components["schemas"]["IndexVersionListResponse"];
export type BuildResponse = components["schemas"]["BuildResponse"];
export type ActivateResponse = components["schemas"]["ActivateResponse"];
export type RollbackResponse = components["schemas"]["RollbackResponse"];
export type CleanupResponse = components["schemas"]["CleanupResponse"];
