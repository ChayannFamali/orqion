import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { apiFetch, parseError } from "../api/client";
import type { ApiError } from "../api/runtime";

describe("parseError", () => {
  it("parses OrqionError JSON body", async () => {
    const response = new Response(
      JSON.stringify({
        error: "model_not_allowed",
        reason: "Модель недоступна для вашей роли",
        constraint: { model: "gpt-4" },
        hint: "Обратитесь к администратору",
      }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    );

    const result = await parseError(response);

    expect(result).toEqual({
      error: "model_not_allowed",
      reason: "Модель недоступна для вашей роли",
      constraint: { model: "gpt-4" },
      hint: "Обратитесь к администратору",
    });
  });

  it("falls back when JSON body is incomplete", async () => {
    const response = new Response(JSON.stringify({ error: "bad_request" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });

    const result = await parseError(response);

    expect(result.error).toBe("bad_request");
    expect(result.reason).toBe("Неизвестная ошибка");
    expect(result.constraint).toBeNull();
    expect(result.hint).toBeNull();
  });

  it("falls back to HTTP status when body is not JSON", async () => {
    const response = new Response("Internal Server Error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    });

    const result = await parseError(response);

    expect(result).toEqual({
      error: "http_error",
      reason: "HTTP 500",
      constraint: null,
      hint: null,
    });
  });
});

describe("apiFetch", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("returns parsed JSON on success", async () => {
    const mockResponse = new Response(
      JSON.stringify({ id: "1", email: "test@orqion.local" }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
    globalThis.fetch = vi.fn().mockResolvedValue(mockResponse);

    const result = await apiFetch<{ id: string; email: string }>("/api/auth/me");

    expect(result).toEqual({ id: "1", email: "test@orqion.local" });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/auth/me",
      expect.objectContaining({
        headers: expect.any(Headers),
        credentials: "include",
      }),
    );
  });

  it("sets Content-Type when body is provided", async () => {
    const mockResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const fetchSpy = vi.fn().mockResolvedValue(mockResponse);
    globalThis.fetch = fetchSpy;

    await apiFetch("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: "a", password: "b" }),
    });

    const callInit = fetchSpy.mock.calls[0][1];
    const headers = callInit.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("does not set Content-Type when body is absent", async () => {
    const mockResponse = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
    const fetchSpy = vi.fn().mockResolvedValue(mockResponse);
    globalThis.fetch = fetchSpy;

    await apiFetch("/api/models");

    const callInit = fetchSpy.mock.calls[0][1];
    const headers = callInit.headers as Headers;
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("throws ApiError on non-ok response", async () => {
    const apiError: ApiError = {
      error: "invalid_credentials",
      reason: "Неверный email или пароль",
      constraint: null,
      hint: null,
    };
    const mockResponse = new Response(JSON.stringify(apiError), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
    globalThis.fetch = vi.fn().mockResolvedValue(mockResponse);

    await expect(apiFetch("/api/auth/login", { method: "POST" })).rejects.toEqual(apiError);
  });

  it("returns undefined for 204 No Content", async () => {
    const mockResponse = new Response(null, { status: 204 });
    globalThis.fetch = vi.fn().mockResolvedValue(mockResponse);

    const result = await apiFetch<void>("/api/auth/logout", { method: "POST" });

    expect(result).toBeUndefined();
  });

  it("throws on network error", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(apiFetch("/api/models")).rejects.toThrow("Failed to fetch");
  });
});
