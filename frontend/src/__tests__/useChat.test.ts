import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChat } from "../hooks/useChat";
import { completeChat, streamChat } from "../api/chat";
import type { ChatMessage } from "../api/types";

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
  completeChat: vi.fn(),
}));

describe("useChat RAF-throttle", () => {
  const originalRAF = globalThis.requestAnimationFrame;
  const originalCancelRAF = globalThis.cancelAnimationFrame;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    globalThis.requestAnimationFrame = originalRAF;
    globalThis.cancelAnimationFrame = originalCancelRAF;
    vi.restoreAllMocks();
  });

  it("batches multiple tokens into a single setStreamingContent per frame", async () => {
    // Мокаем requestAnimationFrame: собираем callbacks, не выполняем сразу
    const rafCallbacks: (() => void)[] = [];
    globalThis.requestAnimationFrame = vi.fn((cb: FrameRequestCallback) => {
      rafCallbacks.push(() => cb(0));
      return rafCallbacks.length;
    });
    globalThis.cancelAnimationFrame = vi.fn();

    // Мокаем streamChat: async generator с 5 токенами
    const tokens = ["Hello", " ", "world", "!", "!"];
    const mockGen = async function* () {
      for (const t of tokens) {
        yield { type: "token" as const, v: t };
      }
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "hi" }] as ChatMessage[],
      });
    });

    // Все 5 токенов получены, но RAF был запрошен только один раз
    // (первый токен → rafRef=null → schedule, последующие → rafRef≠null → skip)
    expect(rafCallbacks.length).toBe(1);

    // Выполняем RAF callback
    act(() => {
      rafCallbacks[0]();
    });

    // После flush — контент полный
    expect(result.current.streamingContent).toBe("Hello world!!");
  });

  it("flushes accumulated content on stream completion", async () => {
    globalThis.requestAnimationFrame = vi.fn((_cb: FrameRequestCallback) => 1);
    globalThis.cancelAnimationFrame = vi.fn();

    const tokens = ["A", "B", "C"];
    const mockGen = async function* () {
      for (const t of tokens) {
        yield { type: "token" as const, v: t };
      }
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "hi" }] as ChatMessage[],
      });
    });

    // RAF не выполнялся, но финальный flush в finally гарантирует контент
    expect(result.current.streamingContent).toBe("ABC");
  });

  it("flushes partial content on abort", async () => {
    globalThis.requestAnimationFrame = vi.fn((_cb: FrameRequestCallback) => 1);
    globalThis.cancelAnimationFrame = vi.fn();

    // Мокаем async generator, который yields 2 токена, затем ждёт
    const mockGen = async function* () {
      yield { type: "token" as const, v: "partial" };
      yield { type: "token" as const, v: "-" };
      // Симулируем abort — генератор завершится
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    const { result } = renderHook(() => useChat());

    act(() => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "hi" }] as ChatMessage[],
      });
    });

    await act(async () => {
      result.current.abort();
    });

    // После abort — partial контент сохранён
    expect(result.current.streamingContent).toBe("partial-");
    expect(result.current.isStreaming).toBe(false);
  });
});

describe("useChat RAG non-streaming", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("completes RAG request and stores sources", async () => {
    const mockResult = {
      type: "complete" as const,
      content: "RAG answer with sources",
      conversation_id: "conv-1",
      sources: [
        {
          chunk_id: "chunk-1",
          document_id: "doc-1",
          structural_path: "readme.md › Setup",
          score: 0.95,
          original_rank: 1,
        },
      ],
      rag_degraded: false,
      rag_errors: [],
    };
    vi.mocked(completeChat).mockResolvedValue(mockResult);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "question" }] as ChatMessage[],
        corpusNames: ["my-corpus"],
      });
    });

    expect(vi.mocked(completeChat)).toHaveBeenCalledTimes(1);
    const callArg = vi.mocked(completeChat).mock.calls[0][0];
    expect(callArg.corpus_names).toEqual(["my-corpus"]);
    expect(callArg.stream).toBe(false);

    expect(result.current.streamingContent).toBe("RAG answer with sources");
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.sources).toEqual(mockResult.sources);
    expect(result.current.ragDegraded).toBe(false);
  });

  it("stores ragDegraded flag when RAG pipeline degrades", async () => {
    const mockResult = {
      type: "complete" as const,
      content: "Degraded answer",
      conversation_id: "conv-2",
      sources: [],
      rag_degraded: true,
      rag_errors: ["embeddings timeout"],
    };
    vi.mocked(completeChat).mockResolvedValue(mockResult);

    const { result } = renderHook(() => useChat());

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "question" }] as ChatMessage[],
        corpusNames: ["my-corpus"],
      });
    });

    expect(result.current.streamingContent).toBe("Degraded answer");
    expect(result.current.ragDegraded).toBe(true);
    expect(result.current.sources).toEqual([]);
  });

  it("passes sources to onDone callback", async () => {
    const mockResult = {
      type: "complete" as const,
      content: "Answer",
      conversation_id: "conv-3",
      sources: [
        {
          chunk_id: "chunk-1",
          document_id: "doc-1",
          structural_path: "file.md",
          score: 0.9,
          original_rank: 1,
        },
      ],
      rag_degraded: false,
      rag_errors: [],
    };
    vi.mocked(completeChat).mockResolvedValue(mockResult);

    const { result } = renderHook(() => useChat());
    const onDone = vi.fn();

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "question" }] as ChatMessage[],
        corpusNames: ["corpus"],
        onDone,
      });
    });

    expect(onDone).toHaveBeenCalledTimes(1);
    const [content, error, sources] = onDone.mock.calls[0];
    expect(content).toBe("Answer");
    expect(error).toBeNull();
    expect(sources).toEqual(mockResult.sources);
  });

  it("does not call streamChat for RAG requests", async () => {
    vi.mocked(completeChat).mockResolvedValue({
      type: "complete",
      content: "RAG",
      sources: [],
      rag_degraded: false,
      rag_errors: [],
    });

    const { result } = renderHook(() => useChat());

    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "q" }] as ChatMessage[],
        corpusNames: ["corpus"],
      });
    });

    expect(vi.mocked(streamChat)).not.toHaveBeenCalled();
    expect(vi.mocked(completeChat)).toHaveBeenCalledTimes(1);
  });
});
