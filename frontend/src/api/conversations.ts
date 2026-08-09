import type {
  ApiError,
  ConversationDetailResponse,
  ConversationListResponse,
  ConversationResponse,
} from "./types";

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

export async function apiListConversations(
  archived: boolean | null = null,
  limit = 50,
  offset = 0,
): Promise<ConversationListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  if (archived !== null) {
    params.set("archived", String(archived));
  }
  const res = await fetch(`/api/conversations?${params.toString()}`);
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as ConversationListResponse;
}

export async function apiGetConversation(id: string): Promise<ConversationDetailResponse> {
  const res = await fetch(`/api/conversations/${id}`);
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as ConversationDetailResponse;
}

export async function apiCreateConversation(
  title: string | null = null,
): Promise<ConversationDetailResponse> {
  const res = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as ConversationDetailResponse;
}

export async function apiUpdateConversation(
  id: string,
  updates: { title?: string; archived?: boolean },
): Promise<ConversationResponse> {
  const res = await fetch(`/api/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as ConversationResponse;
}

export async function apiDeleteConversation(id: string): Promise<void> {
  const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (!res.ok) {
    throw await parseError(res);
  }
}
