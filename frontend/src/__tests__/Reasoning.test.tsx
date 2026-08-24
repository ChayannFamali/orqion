import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { renderHook, act } from "@testing-library/react";
import { ChatMessages } from "../components/ChatMessages";
import { useChat } from "../hooks/useChat";
import { completeChat, streamChat } from "../api/chat";
import type { ChatMessage, MessageResponse } from "../api/types";

/**
 * T-440: reasoning-трейс в чате.
 *
 * Приёмка: блок «Рассуждение» виден только при наличии контента,
 * свёрнут по умолчанию и раскрывается; стрим копит трейс отдельно
 * от ответа (В1 — отдельный тип события).
 */

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
  completeChat: vi.fn(),
}));

function assistantMsg(overrides: Partial<MessageResponse> = {}): MessageResponse {
  return {
    id: "m1",
    role: "assistant",
    content: "Ответ модели",
    model_id: "model-1",
    tokens_in: 10,
    tokens_out: 5,
    created_at: "2026-08-25T10:00:00Z",
    meta: {},
    ...overrides,
  };
}

const baseProps = {
  messages: [] as MessageResponse[],
  streamingContent: "",
  isStreaming: false,
  error: null as { code: string; message: string } | null,
};

describe("T-440: блок «Рассуждение» в ленте", () => {
  it("без reasoning в meta сохранённого сообщения блок скрыт", () => {
    render(<ChatMessages {...baseProps} messages={[assistantMsg()]} />);
    expect(screen.queryByTestId("reasoning-block")).not.toBeInTheDocument();
  });

  it("блок свёрнут по умолчанию и раскрывается по клику", () => {
    render(
      <ChatMessages
        {...baseProps}
        messages={[assistantMsg({ meta: { reasoning_content: "Ход рассуждений" } })]}
      />,
    );
    expect(screen.getByTestId("reasoning-block")).toBeInTheDocument();
    // Свёрнут: текст трейса не виден
    expect(screen.queryByTestId("reasoning-content")).not.toBeInTheDocument();
    expect(screen.queryByText("Ход рассуждений")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Рассуждение/ }));
    expect(screen.getByTestId("reasoning-content")).toHaveTextContent("Ход рассуждений");
  });

  it("в блоке активного ответа скрыт без контента и виден с контентом", () => {
    const { rerender } = render(
      <ChatMessages {...baseProps} streamingContent="Ответ" />,
    );
    expect(screen.queryByTestId("reasoning-block")).not.toBeInTheDocument();

    rerender(
      <ChatMessages
        {...baseProps}
        streamingContent="Ответ"
        streamingReasoning="Трейс рассуждений"
      />,
    );
    expect(screen.getByTestId("reasoning-block")).toBeInTheDocument();
  });
});

describe("T-440: useChat копит рассуждения отдельно от ответа", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("стрим: события reasoning не смешиваются с токенами", async () => {
    const mockGen = async function* () {
      yield { type: "reasoning" as const, v: "Думаю " };
      yield { type: "reasoning" as const, v: "над задачей" };
      yield { type: "token" as const, v: "Ответ: " };
      yield { type: "token" as const, v: "42" };
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    const { result } = renderHook(() => useChat());
    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "вопрос" }] as ChatMessage[],
      });
    });

    expect(result.current.streamingReasoning).toBe("Думаю над задачей");
    expect(result.current.streamingContent).toBe("Ответ: 42");
  });

  it("RAG non-streaming: reasoning_content из ответа", async () => {
    vi.mocked(completeChat).mockResolvedValue({
      type: "complete",
      content: "RAG ответ",
      reasoning_content: "RAG рассуждение",
      sources: [],
      rag_degraded: false,
      rag_errors: [],
    });

    const { result } = renderHook(() => useChat());
    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "вопрос" }] as ChatMessage[],
        corpusNames: ["docs"],
      });
    });

    expect(result.current.streamingContent).toBe("RAG ответ");
    expect(result.current.streamingReasoning).toBe("RAG рассуждение");
  });

  it("без рассуждений у провайдера streamingReasoning пуст", async () => {
    const mockGen = async function* () {
      yield { type: "token" as const, v: "просто ответ" };
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    const { result } = renderHook(() => useChat());
    await act(async () => {
      result.current.sendMessage({
        messages: [{ role: "user", content: "вопрос" }] as ChatMessage[],
      });
    });

    expect(result.current.streamingContent).toBe("просто ответ");
    expect(result.current.streamingReasoning).toBe("");
  });
});
