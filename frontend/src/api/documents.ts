import { apiFetch } from "./client";
import { parseError } from "./client";
import type { DocumentDeleteResponse, DocumentListResponse, DocumentResponse } from "./types";

export async function apiListDocuments(corpusId: string): Promise<DocumentListResponse> {
  return apiFetch<DocumentListResponse>(`/api/corpora/${corpusId}/documents`);
}

export async function apiDeleteDocument(documentId: string): Promise<DocumentDeleteResponse> {
  return apiFetch<DocumentDeleteResponse>(`/api/documents/${documentId}`, { method: "DELETE" });
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

export async function apiUploadDocument(
  corpusId: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append("file", file);

    if (signal) {
      signal.addEventListener("abort", () => {
        xhr.abort();
        reject({ error: "cancelled", reason: "Загрузка отменена", constraint: null, hint: null });
      });
    }

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress({
          loaded: e.loaded,
          total: e.total,
          percent: Math.round((e.loaded / e.total) * 100),
        });
      }
    };

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as DocumentResponse);
        } catch {
          reject(new Error("Failed to parse upload response"));
        }
      } else {
        try {
          const body = JSON.parse(xhr.responseText);
          reject({
            error: body.error ?? "unknown",
            reason: body.reason ?? "Неизвестная ошибка",
            constraint: body.constraint ?? null,
            hint: body.hint ?? null,
          });
        } catch {
          reject({
            error: "http_error",
            reason: `HTTP ${xhr.status}`,
            constraint: null,
            hint: null,
          });
        }
      }
    };

    xhr.onerror = () => {
      reject({
        error: "network_error",
        reason: "Ошибка сети при загрузке файла",
        constraint: null,
        hint: null,
      });
    };

    xhr.open("POST", `/api/corpora/${corpusId}/documents`);
    xhr.send(formData);
  });
}

export { parseError };
