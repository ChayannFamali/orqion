import { apiFetch } from "./client";
import type {
  EvalComparisonRead,
  EvalCompareRequest,
  EvalItemCreate,
  EvalItemRead,
  EvalRunCreate,
  EvalRunListResponse,
  EvalRunRead,
  EvalSetCreateWithItems,
  EvalSetListResponse,
  EvalSetReadWithItems,
} from "./types";

export async function apiListEvalSets(corpusId: string): Promise<EvalSetListResponse> {
  return apiFetch<EvalSetListResponse>(`/api/corpora/${corpusId}/eval-sets`);
}

export async function apiCreateEvalSet(
  corpusId: string,
  body: EvalSetCreateWithItems,
): Promise<EvalSetReadWithItems> {
  return apiFetch<EvalSetReadWithItems>(`/api/corpora/${corpusId}/eval-sets`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

export async function apiGetEvalSet(evalSetId: string): Promise<EvalSetReadWithItems> {
  return apiFetch<EvalSetReadWithItems>(`/api/eval-sets/${evalSetId}`);
}

export async function apiDeleteEvalSet(evalSetId: string): Promise<void> {
  await apiFetch<void>(`/api/eval-sets/${evalSetId}`, { method: "DELETE" });
}

export async function apiCreateEvalItem(
  evalSetId: string,
  body: EvalItemCreate,
): Promise<EvalItemRead> {
  return apiFetch<EvalItemRead>(`/api/eval-sets/${evalSetId}/items`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

export async function apiDeleteEvalItem(
  evalSetId: string,
  itemId: string,
): Promise<void> {
  await apiFetch<void>(`/api/eval-sets/${evalSetId}/items/${itemId}`, {
    method: "DELETE",
  });
}

export async function apiListEvalRuns(evalSetId: string): Promise<EvalRunListResponse> {
  return apiFetch<EvalRunListResponse>(`/api/eval-sets/${evalSetId}/runs`);
}

export async function apiCreateEvalRun(
  evalSetId: string,
  body: EvalRunCreate,
): Promise<EvalRunRead> {
  return apiFetch<EvalRunRead>(`/api/eval-sets/${evalSetId}/runs`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}

export async function apiCompareEvalRuns(
  body: EvalCompareRequest,
): Promise<EvalComparisonRead> {
  return apiFetch<EvalComparisonRead>(`/api/eval-runs/compare`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
}
