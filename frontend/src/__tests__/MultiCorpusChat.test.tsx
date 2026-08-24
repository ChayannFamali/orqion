import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "../pages/ChatPage";
import { apiListAvailableCorpora } from "../api/corpora";
import { completeChat, streamChat } from "../api/chat";

/**
 * T-439: мульти-корпусный RAG в интерфейсе чата.
 *
 * Приёмка: выбранные корпуса уходят в запросе полем corpus_names;
 * без выбора — обычный стриминг-чат.
 */

vi.mock("../api/auth", () => ({
  apiGetMe: vi.fn().mockResolvedValue({ id: "u1", email: "test@orqion.local", is_active: true }),
  apiLogin: vi.fn(),
  apiLogout: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../api/conversations", () => ({
  apiListConversations: vi.fn().mockResolvedValue({ conversations: [], total: 0 }),
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
  apiListAvailableCorpora: vi.fn(),
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

async function sendMessage(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.type(screen.getByPlaceholderText(/Введите сообщение/), text);
  await user.click(screen.getByText("Отправить"));
}

describe("T-439: мульти-корпусный выбор в чате", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiListAvailableCorpora).mockResolvedValue({
      corpora: [
        { id: "c1", name: "docs", data_class: null, ready: true },
        { id: "c2", name: "contracts", data_class: "К2", ready: true },
        { id: "c3", name: "broken", data_class: null, ready: false },
      ],
    });
  });

  it("выбранные корпуса уходят в запросе полем corpus_names", async () => {
    vi.mocked(completeChat).mockResolvedValue({
      type: "complete",
      content: "multi-corpus answer",
      conversation_id: "conv-1",
      sources: [],
      rag_degraded: false,
      rag_errors: [],
    });

    renderChatPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("corpus-selector")).toBeInTheDocument();
    });

    // Два корпуса выбрано, неготовый заблокирован
    await user.click(screen.getByText("docs"));
    await user.click(screen.getByText("contracts · К2"));

    await sendMessage(user, "question");

    await waitFor(() => {
      expect(completeChat).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(completeChat).mock.calls[0][0];
    expect(payload.corpus_names).toEqual(["docs", "contracts"]);
    expect(payload.stream).toBe(false);
    expect(streamChat).not.toHaveBeenCalled();
  });

  it("без выбранных корпусов — обычный стриминг-чат", async () => {
    async function* fakeStream() {
      yield { type: "token" as const, v: "plain" };
    }
    vi.mocked(streamChat).mockReturnValue(fakeStream() as ReturnType<typeof streamChat>);

    renderChatPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("corpus-selector")).toBeInTheDocument();
    });

    await sendMessage(user, "question");

    await waitFor(() => {
      expect(streamChat).toHaveBeenCalledTimes(1);
    });
    expect(completeChat).not.toHaveBeenCalled();
  });

  it("повторный клик снимает выбор корпуса", async () => {
    vi.mocked(completeChat).mockResolvedValue({
      type: "complete",
      content: "answer",
      conversation_id: "conv-1",
      sources: [],
      rag_degraded: false,
      rag_errors: [],
    });

    renderChatPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("corpus-selector")).toBeInTheDocument();
    });

    await user.click(screen.getByText("docs"));
    await user.click(screen.getByText("contracts · К2"));
    // Снимаем второй
    await user.click(screen.getByText("contracts · К2"));

    await sendMessage(user, "question");

    await waitFor(() => {
      expect(completeChat).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(completeChat).mock.calls[0][0];
    expect(payload.corpus_names).toEqual(["docs"]);
  });
});
