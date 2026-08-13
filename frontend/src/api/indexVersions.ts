import { apiFetch } from "./client";
import type {
  ActivateResponse,
  BuildResponse,
  CleanupResponse,
  IndexVersionListResponse,
  IndexVersionResponse,
  RollbackResponse,
} from "./types";

export async function apiBuildIndexVersion(corpusId: string): Promise<BuildResponse> {
  return apiFetch<BuildResponse>(`/api/corpora/${corpusId}/index-versions`, {
    method: "POST",
  });
}

export async function apiListIndexVersions(
  corpusId: string,
): Promise<IndexVersionListResponse> {
  return apiFetch<IndexVersionListResponse>(`/api/corpora/${corpusId}/index-versions`);
}

export async function apiGetIndexVersion(
  corpusId: string,
  versionId: string,
): Promise<IndexVersionResponse> {
  return apiFetch<IndexVersionResponse>(
    `/api/corpora/${corpusId}/index-versions/${versionId}`,
  );
}

export async function apiActivateIndexVersion(
  corpusId: string,
  versionId: string,
): Promise<ActivateResponse> {
  return apiFetch<ActivateResponse>(
    `/api/corpora/${corpusId}/index-versions/${versionId}/activate`,
    { method: "POST" },
  );
}

export async function apiRollbackIndexVersion(
  corpusId: string,
): Promise<RollbackResponse> {
  return apiFetch<RollbackResponse>(
    `/api/corpora/${corpusId}/index-versions/rollback`,
    { method: "POST" },
  );
}

export async function apiCleanupRetiredVersions(
  corpusId: string,
): Promise<CleanupResponse> {
  return apiFetch<CleanupResponse>(
    `/api/corpora/${corpusId}/index-versions/cleanup`,
    { method: "POST" },
  );
}
