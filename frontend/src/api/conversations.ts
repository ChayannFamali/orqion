import { apiFetch } from "./client";
import type {
  ConversationDetailResponse,
  ConversationListResponse,
  ConversationResponse,
} from "./types";

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
  return apiFetch<ConversationListResponse>(`/api/conversations?${params.toString()}`);
}

export async function apiGetConversation(id: string): Promise<ConversationDetailResponse> {
  return apiFetch<ConversationDetailResponse>(`/api/conversations/${id}`);
}

export async function apiCreateConversation(
  title: string | null = null,
): Promise<ConversationDetailResponse> {
  return apiFetch<ConversationDetailResponse>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function apiUpdateConversation(
  id: string,
  updates: { title?: string; archived?: boolean },
): Promise<ConversationResponse> {
  return apiFetch<ConversationResponse>(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
}

export async function apiDeleteConversation(id: string): Promise<void> {
  await apiFetch<void>(`/api/conversations/${id}`, { method: "DELETE" });
}

export interface MessageSearchResult {
  message_id: string;
  conversation_id: string;
  role: string;
  content: string;
  score: number;
}

export async function apiSearchConversations(
  q: string,
  limit = 20,
  offset = 0,
): Promise<MessageSearchResult[]> {
  const params = new URLSearchParams();
  params.set("q", q);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<MessageSearchResult[]>(`/api/conversations/search?${params.toString()}`);
}
