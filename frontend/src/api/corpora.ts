import { apiFetch } from "./client";
import type {
  AvailableCorporaResponse,
  CorpusCreate,
  CorpusListResponse,
  CorpusResponse,
  CorpusUpdate,
} from "./types";

export async function apiListCorpora(): Promise<CorpusListResponse> {
  return apiFetch<CorpusListResponse>("/api/corpora");
}

/** T-439: корпуса, доступные пользователю для чата (по политике роли). */
export async function apiListAvailableCorpora(): Promise<AvailableCorporaResponse> {
  return apiFetch<AvailableCorporaResponse>("/api/corpora/available");
}

export async function apiCreateCorpus(body: CorpusCreate): Promise<CorpusResponse> {
  return apiFetch<CorpusResponse>("/api/corpora", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function apiUpdateCorpus(
  id: string,
  body: CorpusUpdate,
): Promise<CorpusResponse> {
  return apiFetch<CorpusResponse>(`/api/corpora/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
