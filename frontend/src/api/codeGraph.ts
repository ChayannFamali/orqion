import { apiFetch } from "./client";
import type { CodeGraphResponse } from "./types";

/** Т-504: граф связей кода корпуса (активная версия индекса). */
export async function apiGetCodeGraph(corpusId: string): Promise<CodeGraphResponse> {
  return apiFetch<CodeGraphResponse>(`/api/corpora/${corpusId}/code-graph`);
}
