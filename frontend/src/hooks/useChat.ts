import { useCallback, useRef, useState } from "react";
import { completeChat, streamChat } from "../api/chat";
import type { ChatMessage, ChatResponse, ChatSourceEntry, SSEEvent } from "../api/types";

interface UseChatResult {
  /** Накопленный стрим-контент ассистента */
  streamingContent: string;
  /** Признак активого стриминга / запроса */
  isStreaming: boolean;
  /** Ошибка последнего запроса */
  error: { code: string; message: string } | null;
  /** Источники последнего RAG-ответа */
  sources: ChatSourceEntry[] | null;
  /** Признак деградации RAG последнего ответа */
  ragDegraded: boolean;
  /** Отправить сообщение (стриминг или RAG non-streaming) */
  sendMessage: (params: {
    messages: ChatMessage[];
    modelAlias?: string | null;
    conversationId?: string | null;
    /** T-439: мульти-корпусный RAG. Пустой/не заданный список = обычный чат. */
    corpusNames?: string[] | null;
    onDone?: (
      fullContent: string,
      error: { code: string; message: string } | null,
      sources?: ChatSourceEntry[] | null,
    ) => void;
  }) => void;
  /** Прервать стрим */
  abort: () => void;
}

export function useChat(): UseChatResult {
  const [streamingContent, setStreamingContent] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [sources, setSources] = useState<ChatSourceEntry[] | null>(null);
  const [ragDegraded, setRagDegraded] = useState(false);
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
    ({ messages, modelAlias, conversationId, corpusNames, onDone }) => {
      setError(null);
      setStreamingContent("");
      setSources(null);
      setRagDegraded(false);
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      let accumulated = "";
      pendingContentRef.current = "";

      if (corpusNames && corpusNames.length > 0) {
        // RAG-ветка: non-streaming, JSON-ответ с sources
        (async () => {
          try {
            const result: ChatResponse = await completeChat(
              {
                messages,
                model_alias: modelAlias ?? null,
                conversation_id: conversationId ?? null,
                temperature: 0.7,
                stream: false,
                corpus_names: corpusNames,
              },
              controller.signal,
            );

            accumulated = result.content;
            setStreamingContent(accumulated);
            setSources(result.sources ?? null);
            setRagDegraded(result.rag_degraded ?? false);

            onDone?.(accumulated, null, result.sources ?? null);
          } catch (err) {
            if (err instanceof DOMException && err.name === "AbortError") {
              onDone?.(accumulated, null);
            } else {
              const msg = err instanceof Error ? err.message : "Неизвестная ошибка";
              setError({ code: "fetch_error", message: msg });
              onDone?.(accumulated, { code: "fetch_error", message: msg });
            }
          } finally {
            setIsStreaming(false);
            abortRef.current = null;
          }
        })();
        return;
      }

      // Стриминг-ветка: SSE-токены
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

  return { streamingContent, isStreaming, error, sources, ragDegraded, sendMessage, abort };
}
