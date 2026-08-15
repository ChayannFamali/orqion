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

/** Пользователь в списке (T-311). Не из OpenAPI. */
export interface UserListItem {
  id: string;
  email: string;
  is_active: boolean;
  role_id: string;
  role_name: string;
  is_builtin_role: boolean;
}

/** Список пользователей (T-311). */
export interface UserListResponse {
  users: UserListItem[];
}

/** Детали пользователя (T-311). */
export interface UserDetailResponse {
  id: string;
  email: string;
  is_active: boolean;
  role_id: string;
  role_name: string;
  is_builtin_role: boolean;
}

/** Обновление пользователя (T-311). */
export interface UserUpdate {
  role_id?: string;
  is_active?: boolean;
}

/** Корпус (T-312). */
export interface CorpusResponse {
  id: string;
  name: string;
  data_class: string | null;
  pinned_model_id: string | null;
  active_index_version_id: string | null;
}

/** Список корпусов (T-312). */
export interface CorpusListResponse {
  corpora: CorpusResponse[];
}

/** Создание корпуса (T-312). */
export interface CorpusCreate {
  name: string;
  data_class: "К0" | "К1" | "К2" | "К3" | null;
  pinned_model_id?: string | null;
}

/** Обновление data_class корпуса (T-401). */
export interface CorpusUpdate {
  data_class: "К0" | "К1" | "К2" | "К3" | null;
}
