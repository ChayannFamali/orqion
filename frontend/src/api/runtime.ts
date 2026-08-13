/**
 * Клиентские типы, не описанные в OpenAPI-схеме.
 *
 * ApiError — формат ответа exception handler (app/api/exception_handlers.py),
 * не pydantic-схема, поэтому FastAPI не включает его в OpenAPI.
 *
 * SSEEvent — формат стримингового события чата, протокол поверх SSE,
 * не REST-эндпоинт и не входит в OpenAPI.
 */

/** Ошибка API в формате exception handler. */
export interface ApiError {
  error: string;
  reason: string;
  constraint: Record<string, unknown> | null;
  hint: string | null;
}

/** SSE-событие стриминга чата. */
export type SSEEvent =
  | { type: "token"; v: string }
  | { type: "error"; code: string; message: string };

/** Источник цитирования из RAG-ответа (T-306). */
export interface SourceEntry {
  chunk_id: string;
  document_id: string;
  structural_path: string;
  score: number;
  original_rank: number;
}

/** Результат не-стримингового (RAG) чат-запроса (T-306). */
export interface ChatCompletionResult {
  type: "complete" | "error";
  content: string;
  conversation_id?: string;
  model?: string;
  usage?: { tokens_in: number; tokens_out: number };
  sources?: SourceEntry[];
  rag_degraded?: boolean;
  rag_errors?: string[];
}

/** Статус модели после probe (T-308). */
export interface ModelStatus {
  model_id: string;
  alias: string;
  upstream_name: string;
  status: string;
}

/** Результат probe провайдера (T-308). Не из OpenAPI — untyped dict на backend. */
export interface ProbeResult {
  available_models: string[];
  supports_streaming: boolean;
  max_parallel: number;
  model_statuses: ModelStatus[];
  error: string | null;
  observed_context?: Record<string, number | null>;
}

/** Роль с полной политикой (T-310). Не из OpenAPI — policy это dict[str, Any]. */
export interface RoleResponse {
  id: string;
  name: string;
  is_builtin: boolean;
  policy: Record<string, unknown>;
}

/** Список ролей (T-310). */
export interface RoleListResponse {
  roles: RoleResponse[];
}

/** Создание роли (T-310). */
export interface RoleCreate {
  name: string;
  policy: Record<string, unknown>;
}

/** Обновление политики роли (T-310). */
export interface RoleUpdate {
  policy: Record<string, unknown>;
}
