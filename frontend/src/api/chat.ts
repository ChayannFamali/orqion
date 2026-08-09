import type { ApiError, ChatRequest, SSEEvent } from "./types";

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as Partial<ApiError>;
    return {
      error: body.error ?? "unknown",
      reason: body.reason ?? "Неизвестная ошибка",
      constraint: body.constraint ?? null,
      hint: body.hint ?? null,
    };
  } catch {
    return {
      error: "http_error",
      reason: `HTTP ${response.status}`,
      constraint: null,
      hint: null,
    };
  }
}

/**
 * Парсит одно SSE-событие из строки data-блока.
 *
 * Формат бэкенда: `data: {json}\n\n` или `data: [DONE]\n\n`.
 */
function parseSSEData(data: string): SSEEvent | null {
  if (data === "[DONE]") {
    return null;
  }
  try {
    return JSON.parse(data) as SSEEvent;
  } catch {
    return null;
  }
}

/**
 * Отправляет чат-запрос и стримит токены через SSE.
 *
 * Асинхронный генератор: for await (const event of streamChat(...)) { ... }
 * Бэкенд завершает поток `data: [DONE]\n\n`.
 */
export async function* streamChat(
  body: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent, void, unknown> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  });

  if (!res.ok) {
    throw await parseError(res);
  }

  if (!res.body) {
    throw new Error("No response body");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });

      let newlineIdx: number;
      while ((newlineIdx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, newlineIdx);
        buffer = buffer.slice(newlineIdx + 2);

        const dataLine = block
          .split("\n")
          .find((l) => l.startsWith("data: "))
          ?.slice(6);

        if (dataLine) {
          const event = parseSSEData(dataLine);
          if (event !== null) {
            yield event;
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Не-стриминговый чат-запрос. Возвращает полный ответ.
 */
export async function completeChat(
  body: ChatRequest,
  signal?: AbortSignal,
): Promise<{ content: string; conversation_id?: string }> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, stream: false }),
    signal,
  });

  if (!res.ok) {
    throw await parseError(res);
  }
  const data = (await res.json()) as {
    content: string;
    conversation_id?: string;
    type?: string;
    code?: string;
    message?: string;
  };
  return { content: data.content, conversation_id: data.conversation_id };
}
