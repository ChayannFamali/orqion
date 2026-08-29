import { apiFetch } from "./client";
import type { DocumentGraphResponse } from "./types";

/** Т-505: граф связей документов корпуса (семантические кластеры). */
export async function apiGetDocumentGraph(corpusId: string): Promise<DocumentGraphResponse> {
  return apiFetch<DocumentGraphResponse>(`/api/corpora/${corpusId}/document-graph`);
}
