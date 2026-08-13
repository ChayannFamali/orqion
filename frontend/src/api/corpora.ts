import { apiFetch } from "./client";
import type { CorpusCreate, CorpusListResponse, CorpusResponse } from "./types";

export async function apiListCorpora(): Promise<CorpusListResponse> {
  return apiFetch<CorpusListResponse>("/api/corpora");
}

export async function apiCreateCorpus(body: CorpusCreate): Promise<CorpusResponse> {
  return apiFetch<CorpusResponse>("/api/corpora", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
