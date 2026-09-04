/**
 * Re-export типов API.
 *
 * Сгенерированные типы — из generated.ts (T-301, openapi-typescript).
 * Клиентские типы (ApiError, SSEEvent) — из runtime.ts, не описаны в OpenAPI.
 *
 * Этот файл — точка импорта для всех потребителей. При изменении схемы
 * типы обновляются автоматически через `npm run gen:types`.
 */

export type { ApiError, SSEEvent, ModelStatus, ProbeAvailableModel, ProbeResult, RoleResponse, RoleListResponse, RoleCreate, RoleUpdate, UserListItem, UserListResponse, UserDetailResponse, UserUpdate, UserCreateRequest, UserCreateResponse, ChangePasswordRequest, ChangePasswordResponse, CorpusResponse, CorpusListResponse, CorpusCreate, CorpusUpdate } from "./runtime";

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
export type ChatResponse = components["schemas"]["ChatResponse"];
export type ChatUsage = components["schemas"]["ChatUsage"];
export type ChatSourceEntry = components["schemas"]["ChatSourceEntry"];
export type ModelInfo = components["schemas"]["ModelResponse"];
export type ModelResponse = components["schemas"]["ModelResponse"];
export type ProviderInfo = components["schemas"]["ProviderResponse"];
export type ProviderResponse = components["schemas"]["ProviderResponse"];
export type ProviderListResponse = components["schemas"]["ProviderListResponse"];
export type ProviderCreate = components["schemas"]["ProviderCreate"];
export type ProviderUpdate = components["schemas"]["ProviderUpdate"];
/** Канонический набор видов провайдера (T-437) — выводится из схемы. */
export type ProviderKind = ProviderCreate["kind"];
/** Единый статус скачивания модели (T-437, часть А). */
export type DownloadStatusResponse = components["schemas"]["DownloadStatusResponse"];
export type ModelCreate = components["schemas"]["ModelCreate"];
export type ModelUpdate = components["schemas"]["ModelUpdate"];
/** Результат удаления модели провайдера (T-443, коммит 2). */
export type ModelDeleteResponse = components["schemas"]["ModelDeleteResponse"];
/** Корпус, доступный пользователю для чата (T-439). */
export type AvailableCorpusEntry = components["schemas"]["AvailableCorpusEntry"];
export type AvailableCorporaResponse = components["schemas"]["AvailableCorporaResponse"];
/** Результат удаления корпуса. */
export type CorpusDeleteResponse = components["schemas"]["CorpusDeleteResponse"];
/** Диагностика окружения хоста (T-444, только чтение). */
export type EnvironmentDiagnosticsResponse =
  components["schemas"]["EnvironmentDiagnosticsResponse"];
export type NvidiaDiagnostics = components["schemas"]["NvidiaDiagnostics"];
export type GpuInfo = components["schemas"]["GpuInfo"];
export type SpanResponse = components["schemas"]["SpanResponse"];
export type TraceSummaryResponse = components["schemas"]["TraceSummaryResponse"];
export type TraceListResponse = components["schemas"]["TraceListResponse"];
export type TraceDetailResponse = components["schemas"]["TraceDetailResponse"];
export type ProviderDeleteResponse = components["schemas"]["ProviderDeleteResponse"];
export type DocumentResponse = components["schemas"]["DocumentResponse"];
export type DocumentDetailResponse = components["schemas"]["DocumentDetailResponse"];
export type DocumentDeleteResponse = components["schemas"]["DocumentDeleteResponse"];
export type DocumentListResponse = components["schemas"]["DocumentListResponse"];
export type IndexVersionResponse = components["schemas"]["IndexVersionResponse"];
export type IndexVersionListResponse = components["schemas"]["IndexVersionListResponse"];
export type BuildResponse = components["schemas"]["BuildResponse"];
export type ActivateResponse = components["schemas"]["ActivateResponse"];
export type RollbackResponse = components["schemas"]["RollbackResponse"];
export type CleanupResponse = components["schemas"]["CleanupResponse"];
export type EvalSetRead = components["schemas"]["EvalSetRead"];
export type EvalSetReadWithItems = components["schemas"]["EvalSetReadWithItems"];
export type EvalSetListResponse = components["schemas"]["EvalSetListResponse"];
export type EvalSetCreateWithItems = components["schemas"]["EvalSetCreateWithItems"];
export type EvalItemRead = components["schemas"]["EvalItemRead"];
export type EvalItemCreate = components["schemas"]["EvalItemCreate"];
export type EvalRunRead = components["schemas"]["EvalRunRead"];
export type EvalRunCreate = components["schemas"]["EvalRunCreate"];
export type EvalRunListResponse = components["schemas"]["EvalRunListResponse"];
export type EvalComparisonRead = components["schemas"]["EvalComparisonRead"];
export type EvalCompareRequest = components["schemas"]["EvalCompareRequest"];
export type AnalyticsResponse = components["schemas"]["AnalyticsResponse"];
export type AnalyticsSummary = components["schemas"]["AnalyticsSummary"];
export type DailyBreakdown = components["schemas"]["DailyBreakdown"];
export type ModelBreakdown = components["schemas"]["ModelBreakdown"];
export type UserBreakdown = components["schemas"]["UserBreakdown"];
export type AuditLogResponse = components["schemas"]["AuditLogResponse"];
export type AuditLogListResponse = components["schemas"]["AuditLogListResponse"];
export type AuditActionsResponse = components["schemas"]["AuditActionsResponse"];
/** Настройки RAG-поиска уровня рабочей области (Т-506). */
export type RagSettingsResponse = components["schemas"]["RagSettingsResponse"];
export type RagSettingsUpdate = components["schemas"]["RagSettingsUpdate"];
/** Граф связей кода (Т-504). */
export type CodeGraphNode = components["schemas"]["CodeGraphNode"];
export type CodeGraphEdge = components["schemas"]["CodeGraphEdge"];
export type CodeGraphResponse = components["schemas"]["CodeGraphResponse"];
/** Граф связей документов — семантические кластеры (Т-505). */
export type DocumentGraphNode = components["schemas"]["DocumentGraphNode"];
export type DocumentGraphEdge = components["schemas"]["DocumentGraphEdge"];
export type DocumentGraphResponse = components["schemas"]["DocumentGraphResponse"];
/** Библиотека сохранённых промптов (Т-507). */
export type PromptTemplateResponse = components["schemas"]["PromptTemplateResponse"];
export type PromptTemplateListResponse = components["schemas"]["PromptTemplateListResponse"];
export type PromptTemplateCreate = components["schemas"]["PromptTemplateCreate"];
export type PromptTemplateUpdate = components["schemas"]["PromptTemplateUpdate"];
/** Агентный модуль (Т-502). */
export type AgentChatRequest = components["schemas"]["AgentChatRequest"];
export type AgentChatResponse = components["schemas"]["AgentChatResponse"];
export type AgentStepEntry = components["schemas"]["AgentStepEntry"];
export type PendingConfirmation = components["schemas"]["PendingConfirmation"];
/** Реестр серверов внешних инструментов (Т-503). */
export type McpServerResponse = components["schemas"]["McpServerResponse"];
export type McpServerListResponse = components["schemas"]["McpServerListResponse"];
export type McpServerCreate = components["schemas"]["McpServerCreate"];
export type McpServerUpdate = components["schemas"]["McpServerUpdate"];
export type McpServerDeleteResponse = components["schemas"]["McpServerDeleteResponse"];
