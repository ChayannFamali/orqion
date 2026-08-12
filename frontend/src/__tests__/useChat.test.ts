import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChat } from "../hooks/useChat";
import { streamChat } from "../api/chat";
import type { ChatMessage } from "../api/types";

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
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
