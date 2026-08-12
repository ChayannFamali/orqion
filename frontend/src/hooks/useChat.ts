import { useCallback, useRef, useState } from "react";
import { streamChat } from "../api/chat";
import type { ChatMessage, SSEEvent } from "../api/types";

interface UseChatResult {
  /** Накопленный стрим-контент ассистента */
  streamingContent: string;
  /** Признак активного стриминга */
  isStreaming: boolean;
  /** Ошибка последнего запроса */
  error: { code: string; message: string } | null;
  /** Отправить сообщение в стрим */
  sendMessage: (params: {
    messages: ChatMessage[];
    modelAlias?: string | null;
    conversationId?: string | null;
    onDone?: (fullContent: string, error: { code: string; message: string } | null) => void;
  }) => void;
  /** Прервать стрим */
  abort: () => void;
}

export function useChat(): UseChatResult {
  const [streamingContent, setStreamingContent] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const sendMessage = useCallback<UseChatResult["sendMessage"]>(
    ({ messages, modelAlias, conversationId, onDone }) => {
      setError(null);
      setStreamingContent("");
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      let accumulated = "";

      (async () => {
        try {
          const stream = streamChat(
            {
              messages,
              model_alias: modelAlias ?? null,
              conversation_id: conversationId ?? null,
              temperature: 0.7,
              stream: true,
            },
            controller.signal,
          );

          for await (const event of stream) {
            const e: SSEEvent = event;
            if (e.type === "token") {
              accumulated += e.v;
              setStreamingContent(accumulated);
            } else if (e.type === "error") {
              setError({ code: e.code, message: e.message });
            }
          }

          onDone?.(accumulated, null);
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            onDone?.(accumulated, null);
          } else {
            const msg =
              err instanceof Error ? err.message : "Неизвестная ошибка";
            setError({ code: "fetch_error", message: msg });
            onDone?.(accumulated, { code: "fetch_error", message: msg });
          }
        } finally {
          setIsStreaming(false);
          abortRef.current = null;
        }
      })();
    },
    [],
  );

  return { streamingContent, isStreaming, error, sendMessage, abort };
}
