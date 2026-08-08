export interface HealthResponse {
  status: string;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/health");
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status}`);
  }
  return res.json() as Promise<HealthResponse>;
}
