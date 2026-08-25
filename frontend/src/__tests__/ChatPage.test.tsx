import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "../pages/ChatPage";

vi.mock("../api/auth", () => ({
  apiGetMe: vi.fn().mockResolvedValue({ id: "u1", email: "test@orqion.local", is_active: true }),
  apiLogin: vi.fn(),
  apiLogout: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../api/conversations", () => ({
  apiListConversations: vi.fn().mockResolvedValue({
    conversations: [
      {
        id: "c1",
        title: "Test conversation",
        archived: false,
        created_at: "2026-08-08T10:00:00Z",
        message_count: 2,
      },
    ],
    total: 1,
  }),
  apiGetConversation: vi.fn().mockResolvedValue({
    id: "c1",
    title: "Test conversation",
    archived: false,
    created_at: "2026-08-08T10:00:00Z",
    message_count: 2,
    messages: [
      {
        id: "msg1",
        role: "user",
        content: "Hello",
        model_id: null,
        tokens_in: null,
        tokens_out: null,
        created_at: "2026-08-08T10:00:00Z",
        meta: {},
      },
      {
        id: "msg2",
        role: "assistant",
        content: "Hi there!",
        model_id: "m1",
        tokens_in: 5,
        tokens_out: 3,
        created_at: "2026-08-08T10:00:00Z",
        meta: {},
      },
    ],
  }),
  apiCreateConversation: vi.fn(),
  apiUpdateConversation: vi.fn(),
  apiDeleteConversation: vi.fn(),
  apiResetConversationContext: vi.fn().mockResolvedValue({
    id: "c1",
    title: "Test conversation",
    archived: false,
    created_at: "2026-08-08T10:00:00Z",
    message_count: 2,
    context_reset_at: "2026-08-08T11:00:00Z",
  }),
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

describe("ChatPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders sidebar with conversations and new chat button", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getAllByText("Новый диалог").length).toBeGreaterThan(0);
      expect(screen.getByText("Test conversation")).toBeInTheDocument();
    });
  });

  it("renders model selector with available models", async () => {
    renderChatPage();
    await waitFor(() => {
      const select = screen.getByRole("combobox");
      expect(select).toBeInTheDocument();
      expect(select.textContent).toContain("qwen-7b");
    });
  });

  it("renders chat input with placeholder", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Введите сообщение/)).toBeInTheDocument();
    });
  });

  it("shows conversation messages when clicking a conversation", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getByText("Test conversation")).toBeInTheDocument();
    });
    const user = userEvent.setup();
    await user.click(screen.getByText("Test conversation"));
    await waitFor(() => {
      expect(screen.getByText("Hello")).toBeInTheDocument();
      expect(screen.getByText("Hi there!")).toBeInTheDocument();
    });
  });

  it("new conversation button opens modal with required model selection", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getAllByText("Новый диалог").length).toBeGreaterThan(0);
    });
    const user = userEvent.setup();
    // Кнопка в панели диалогов — первая в DOM
    const buttons = screen.getAllByText("Новый диалог");
    await user.click(buttons[0]);
    await waitFor(() => {
      expect(screen.getByText("Выберите модель — выбор обязателен")).toBeInTheDocument();
      expect(screen.getByText("Создать диалог")).toBeDisabled();
    });
    await user.click(screen.getByText("qwen-7b"));
    const create = screen.getByText("Создать диалог");
    expect(create).toBeEnabled();
    await user.click(create);
    await waitFor(() => {
      expect(screen.queryByText("Выберите модель — выбор обязателен")).not.toBeInTheDocument();
    });
  });

  it("model selector defaults to first available model", async () => {
    renderChatPage();
    await waitFor(() => {
      const select = screen.getByRole("combobox") as HTMLSelectElement;
      expect(select.value).toBe("qwen-7b");
    });
  });

  it("regenerate sends messages without last assistant response", async () => {
    const { streamChat } = await import("../api/chat");
    const mockGen = async function* () {
      yield { type: "token" as const, v: "New answer" };
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    renderChatPage();

    // Select conversation with existing messages
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText("Test conversation")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Test conversation"));

    // Wait for messages to load
    await waitFor(() => {
      expect(screen.getByText("Hi there!")).toBeInTheDocument();
    });

    // Send a message first to populate localMessages
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Test question");
    await user.click(screen.getByText("Отправить"));

    // Wait for streaming to complete
    await waitFor(() => {
      expect(screen.getByText("New answer")).toBeInTheDocument();
    });

    // Click regenerate
    const regenButton = screen.getByTitle("Повторить");
    await user.click(regenButton);

    // streamChat should have been called again
    await waitFor(() => {
      expect(streamChat).toHaveBeenCalledTimes(2);
    });

    // The second call should not include the previous assistant response
    const secondCallArgs = vi.mocked(streamChat).mock.calls[1][0];
    const lastMsg = secondCallArgs.messages[secondCallArgs.messages.length - 1];
    expect(lastMsg.role).toBe("user");
  });

  it("edit truncates messages after edited user message and resends", async () => {
    const { streamChat } = await import("../api/chat");
    const mockGen = async function* () {
      yield { type: "token" as const, v: "Edited answer" };
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    renderChatPage();

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText("Test conversation")).toBeInTheDocument();
    });
    await user.click(screen.getByText("Test conversation"));

    await waitFor(() => {
      expect(screen.getByText("Hello")).toBeInTheDocument();
    });

    // Send a message to get localMessages populated
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "First question");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(screen.getByText("Edited answer")).toBeInTheDocument();
    });

    // Now edit: click edit button on the first user message in localMessages
    const editButton = screen.getByTitle("Редактировать");
    await user.click(editButton);

    // Edit the message — find the edit textarea (contains original "Hello" text)
    const textareas = screen.getAllByRole("textbox");
    const editTextarea = textareas.find(
      (el) => (el as HTMLTextAreaElement).value === "Hello",
    ) as HTMLTextAreaElement;
    expect(editTextarea).toBeDefined();
    await user.clear(editTextarea);
    await user.type(editTextarea, "Edited question");
    // Click the "Отправить" button inside the edit form (first matching)
    const sendButtons = screen.getAllByText("Отправить");
    // Edit form button is the first one (chat input is disabled and second)
    await user.click(sendButtons[0]);

    // streamChat should have been called again
    await waitFor(() => {
      expect(streamChat).toHaveBeenCalledTimes(2);
    });

    // The second call should have the edited content as last user message
    const secondCallArgs = vi.mocked(streamChat).mock.calls[1][0];
    const lastMsg = secondCallArgs.messages[secondCallArgs.messages.length - 1];
    expect(lastMsg.content).toBe("Edited question");
  });

  it("reasoning toggle is hidden when policy fixes the mode (default off) — Т-445", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Введите сообщение/)).toBeInTheDocument();
    });
    // apiGetMe мок возвращает пользователя без поля "раз в политике" → "off"
    expect(screen.queryByTestId("reasoning-toggle")).not.toBeInTheDocument();
  });

  it("reasoning toggle visible under 'optional' policy and sends reasoning_mode — Т-445", async () => {
    const { apiGetMe } = await import("../api/auth");
    const { streamChat } = await import("../api/chat");
    vi.mocked(apiGetMe).mockResolvedValue({
      id: "u1",
      email: "test@orqion.local",
      is_active: true,
      capabilities: ["chat"],
      reasoning: "optional",
    } as any);
    const mockGen = async function* () {
      yield { type: "token" as const, v: "With reasoning" };
    };
    vi.mocked(streamChat).mockReturnValue(mockGen() as any);

    renderChatPage();

    // Переключатель виден при политике "optional"
    await waitFor(() => {
      expect(screen.getByTestId("reasoning-toggle")).toBeInTheDocument();
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("reasoning-toggle"));

    // Отправляем сообщение — режим "on" уходит в запрос
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Think hard");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(streamChat).toHaveBeenCalledTimes(1);
    });
    const request = vi.mocked(streamChat).mock.calls[0][0];
    expect(request.reasoning_mode).toBe("on");
  });
});
