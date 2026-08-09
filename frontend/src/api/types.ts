/**
 * Минимальные типы API orqion.
 *
 * T-301 заменит их на сгенерированные из OpenAPI-схемы FastAPI.
 * Пока — ручное описание контрактов бэкенда.
 */

/* === Auth === */

export interface LoginRequest {
  email: string;
  password: string;
}

export interface UserResponse {
  id: string;
  email: string;
  is_active: boolean;
}

export interface LoginResponse {
  user: UserResponse;
}

/* === Conversations === */

export interface MessageResponse {
  id: string;
  role: string;
  content: string;
  model_id: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  created_at: string;
  meta: Record<string, unknown>;
}

export interface ConversationResponse {
  id: string;
  title: string;
  archived: boolean;
  created_at: string;
  message_count: number;
}

export interface ConversationDetailResponse extends ConversationResponse {
  messages: MessageResponse[];
}

export interface ConversationListResponse {
  conversations: ConversationResponse[];
  total: number;
}

/* === Chat === */

export interface ChatMessage {
  role: string;
  content: string;
}

export interface ChatRequest {
  conversation_id?: string | null;
  messages: ChatMessage[];
  model_alias?: string | null;
  max_tokens?: number | null;
  temperature?: number;
  stream?: boolean;
}

export type SSEEvent =
  | { type: "token"; v: string }
  | { type: "error"; code: string; message: string };

/* === Models (from providers) === */

export interface ModelInfo {
  id: string;
  alias: string;
  upstream_name: string;
  locality: string;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  supports_reasoning: boolean;
  cost_in: number | null;
  cost_out: number | null;
  enabled: boolean;
}

export interface ProviderInfo {
  id: string;
  kind: string;
  base_url: string;
  enabled: boolean;
  capabilities: Record<string, unknown>;
  models: ModelInfo[];
}

export interface ProviderListResponse {
  providers: ProviderInfo[];
}

/* === Errors === */

export interface ApiError {
  error: string;
  reason: string;
  constraint: Record<string, unknown> | null;
  hint: string | null;
}
