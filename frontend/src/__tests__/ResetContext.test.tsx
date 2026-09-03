import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "../pages/ChatPage";
import { apiGetConversation, apiResetConversationContext } from "../api/conversations";

/**
 * T-442: мягкий сброс контекста диалога.
 *
 * Ключевой приёмочный тест — состав payload: после сброса в запрос к модели
 * уходят только сообщения после маркера (буфер отправки обнуляется).
 */

vi.mock("../api/auth", () => ({
  apiGetMe: vi.fn().mockResolvedValue({ id: "u1", email: "test@orqion.local", is_active: true }),
  apiLogin: vi.fn(),
  apiLogout: vi.fn().mockResolvedValue(undefined),
}));

const baseConversation = {
  id: "c1",
  title: "Test conversation",
  archived: false,
  mode: "chat",
  created_at: "2026-08-08T09:00:00Z",
  message_count: 2,
  context_reset_at: null as string | null,
  messages: [
    {
      id: "msg1",
      role: "user",
      content: "Старый вопрос",
      model_id: null,
      tokens_in: null,
      tokens_out: null,
      created_at: "2026-08-08T10:00:00Z",
      meta: {},
    },
    {
      id: "msg2",
      role: "assistant",
      content: "Старый ответ",
      model_id: "m1",
      tokens_in: 5,
      tokens_out: 3,
      created_at: "2026-08-08T10:05:00Z",
      meta: {},
    },
  ],
};

vi.mock("../api/conversations", () => ({
  apiListConversations: vi.fn().mockResolvedValue({
    conversations: [
      {
        id: "c1",
        title: "Test conversation",
        archived: false,
        created_at: "2026-08-08T09:00:00Z",
        message_count: 2,
      },
    ],
    total: 1,
  }),
  apiGetConversation: vi.fn(),
  apiCreateConversation: vi.fn(),
  apiUpdateConversation: vi.fn(),
  apiDeleteConversation: vi.fn(),
  apiResetConversationContext: vi.fn(),
}));

vi.mock("../api/models", () => ({
  apiListAvailableModels: vi.fn().mockResolvedValue([
    {
      id: "m1",
      alias: "qwen-7b",
      upstream_name: "qwen2.5-7b",
      locality: "local",
      max_input_tokens: 32768,
      max_output_tokens: 4096,
      supports_reasoning: false,
      cost_in: null,
      cost_out: null,
      enabled: true,
    },
  ]),
}));

vi.mock("../api/corpora", () => ({
  apiListAvailableCorpora: vi.fn().mockResolvedValue({ corpora: [] }),
}));

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
  completeChat: vi.fn(),
}));

function renderChatPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ChatPage />
    </QueryClientProvider>,
  );
  return client;
}

async function openConversation(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getByText("Test conversation")).toBeInTheDocument();
  });
  await user.click(screen.getByText("Test conversation"));
  await waitFor(() => {
    expect(screen.getByText("Старый вопрос")).toBeInTheDocument();
  });
}

function mockStreamAnswer(answer: string) {
  return async function* () {
    yield { type: "token" as const, v: answer };
  };
}

describe("T-442: мягкий сброс контекста", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGetConversation).mockResolvedValue(structuredClone(baseConversation));
    vi.mocked(apiResetConversationContext).mockResolvedValue({
      ...structuredClone(baseConversation),
      message_count: 2,
      context_reset_at: "2026-08-08T11:00:00Z",
    });
  });

  it("после сброса в запрос уходят только сообщения после маркера", async () => {
    const { streamChat } = await import("../api/chat");
    vi.mocked(streamChat).mockReturnValue(mockStreamAnswer("Ответ")() as any);

    renderChatPage();
    const user = userEvent.setup();
    await openConversation(user);

    // Первое сообщение в сессии — попадает в буфер отправки
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Первый вопрос");
    await user.click(screen.getByText("Отправить"));
    await waitFor(() => {
      expect(screen.getByText("Ответ")).toBeInTheDocument();
    });
    expect(streamChat).toHaveBeenCalledTimes(1);
    const firstPayload = vi.mocked(streamChat).mock.calls[0][0];
    expect(firstPayload.messages).toHaveLength(1);

    // Сброс контекста
    await user.click(screen.getByTitle(/Сбросить контекст/));
    await waitFor(() => {
      expect(apiResetConversationContext).toHaveBeenCalledWith("c1");
    });
    // Дождаться обнуления буфера отправки (индикатор показывает 0)
    await waitFor(() => {
      expect(
        screen.getByTitle(/Оценка занятости окна контекста/).textContent,
      ).toMatch(/≈\s*0\s*\//);
    });

    // Второе сообщение: история до маркера в payload не входит
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Второй вопрос");
    await user.click(screen.getByText("Отправить"));
    await waitFor(() => {
      expect(streamChat).toHaveBeenCalledTimes(2);
    });
    const secondPayload = vi.mocked(streamChat).mock.calls[1][0];
    expect(secondPayload.messages).toEqual([{ role: "user", content: "Второй вопрос" }]);
  });

  it("разделитель «Контекст сброшен» между сообщениями до и после маркера", async () => {
    const { apiGetConversation } = await import("../api/conversations");
    const conv = structuredClone(baseConversation);
    conv.context_reset_at = "2026-08-08T10:02:00Z"; // между msg1 и msg2
    vi.mocked(apiGetConversation).mockResolvedValue(conv);

    renderChatPage();
    const user = userEvent.setup();
    await openConversation(user);

    const dividers = screen.getAllByTestId("context-reset-divider");
    expect(dividers).toHaveLength(1);
    // msg1 до маркера, msg2 после — разделитель между ними (не на краю ленты)
    const list = dividers[0].parentElement as HTMLElement;
    const items = Array.from(list.children);
    const dividerIdx = items.indexOf(dividers[0]);
    expect(dividerIdx).toBeGreaterThan(0);
    expect(dividerIdx).toBeLessThan(items.length - 1);
  });

  it("сразу после сброса (нет новых сообщений) разделитель в конце ленты", async () => {
    const { apiGetConversation } = await import("../api/conversations");
    const conv = structuredClone(baseConversation);
    conv.context_reset_at = "2026-08-08T11:00:00Z"; // после всех сообщений
    vi.mocked(apiGetConversation).mockResolvedValue(conv);

    renderChatPage();
    const user = userEvent.setup();
    await openConversation(user);

    expect(screen.getAllByTestId("context-reset-divider")).toHaveLength(1);
  });

  it("индикатор контекста обнуляется после сброса", async () => {
    const { streamChat } = await import("../api/chat");
    vi.mocked(streamChat).mockReturnValue(mockStreamAnswer("Ответ на вопрос")() as any);

    renderChatPage();
    const user = userEvent.setup();
    await openConversation(user);

    const indicator = () => screen.getByTitle(/Оценка занятости окна контекста/);
    // Буфер пуст → 0
    expect(indicator().textContent).toMatch(/≈\s*0\s*\//);

    // Отправка добавляет сообщение в буфер → оценка растёт
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос подлиннее");
    await user.click(screen.getByText("Отправить"));
    await waitFor(() => {
      expect(indicator().textContent).not.toMatch(/≈\s*0\s*\//);
    });
    expect(indicator().textContent).toMatch(/32\s*768/);

    // Сброс → буфер обнуляется → 0
    await user.click(screen.getByTitle(/Сбросить контекст/));
    await waitFor(() => {
      expect(indicator().textContent).toMatch(/≈\s*0\s*\//);
    });
  });

  it("кнопка сброса недоступна для пустого диалога", async () => {
    const { apiGetConversation } = await import("../api/conversations");
    const conv = structuredClone(baseConversation);
    conv.messages = [];
    conv.message_count = 0;
    vi.mocked(apiGetConversation).mockResolvedValue(conv);

    renderChatPage();
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText("Test conversation")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Test conversation"));
    await waitFor(() => {
      expect(screen.getByTitle(/Сбросить контекст/)).toBeDisabled();
    });
  });
});

describe("estimateTokens", () => {
  it("пустая строка — 0; 3 символа ≈ 1 токен", async () => {
    const { estimateTokens } = await import("../utils/estimateTokens");
    expect(estimateTokens("")).toBe(0);
    expect(estimateTokens("abc")).toBe(1);
    expect(estimateTokens("абвгд")).toBe(2); // 5 / 3 → 2
  });
});
