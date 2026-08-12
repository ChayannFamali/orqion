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

  // RAF-throttle: один setStreamingContent на кадр, не на каждый токен
  const rafRef = useRef<number | null>(null);
  const pendingContentRef = useRef("");

  const flushContent = useCallback(() => {
    rafRef.current = null;
    setStreamingContent(pendingContentRef.current);
  }, []);

  const abort = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    // Сброс RAF
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
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
      pendingContentRef.current = "";

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
              pendingContentRef.current = accumulated;
              // Троттлинг: планируем flush на следующий кадр, если ещё не запланирован
              if (rafRef.current === null) {
                rafRef.current = requestAnimationFrame(flushContent);
              }
            } else if (e.type === "error") {
              setError({ code: e.code, message: e.message });
            }
          }

          // Финальный flush: гарантия, что весь контент отображён
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
          }
          setStreamingContent(accumulated);

          onDone?.(accumulated, null);
        } catch (err) {
          if (err instanceof DOMException && err.name === "AbortError") {
            // Flush того, что накопилось до отмены
            if (rafRef.current !== null) {
              cancelAnimationFrame(rafRef.current);
              rafRef.current = null;
            }
            setStreamingContent(accumulated);
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
    [flushContent],
  );

  return { streamingContent, isStreaming, error, sendMessage, abort };
}
