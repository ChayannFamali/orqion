import { apiFetch } from "./client";
import type { CorpusCreate, CorpusListResponse, CorpusResponse, CorpusUpdate } from "./types";

export async function apiListCorpora(): Promise<CorpusListResponse> {
  return apiFetch<CorpusListResponse>("/api/corpora");
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
