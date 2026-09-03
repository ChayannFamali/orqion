/**
 * Т-502 (решение 10): точка входа в агентный диалог.
 *
 * Кнопка «Агентный диалог» видна только при наличии модели с флагом
 * ``supports_tools``; создание переключает режим; отправка идёт в
 * агентный эндпоинт и показывает шаги прогона.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "../pages/ChatPage";

vi.mock("../api/auth", () => ({
  apiGetMe: vi
    .fn()
    .mockResolvedValue({ id: "u1", email: "test@orqion.local", is_active: true }),
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
  apiSearchConversations: vi.fn().mockResolvedValue([]),
}));

vi.mock("../api/models", () => ({
  apiListAvailableModels: vi.fn(),
}));

vi.mock("../api/corpora", () => ({
  apiListAvailableCorpora: vi.fn().mockResolvedValue({ corpora: [] }),
}));

vi.mock("../api/chat", () => ({
  streamChat: vi.fn(),
  completeChat: vi.fn(),
}));

vi.mock("../api/agent", () => ({
  agentChat: vi.fn(),
}));

/** Модель с флагом инструментов и модель без него. */
const AGENT_MODEL = {
  id: "m1",
  alias: "local/agent-model",
  upstream_name: "agent",
  locality: "local",
  max_input_tokens: 32768,
  max_output_tokens: 4096,
  supports_reasoning: false,
  reasoning_toggleable: false,
  supports_tools: true,
  cost_in: null,
  cost_out: null,
  enabled: true,
};
const PLAIN_MODEL = {
  id: "m2",
  alias: "local/plain-model",
  upstream_name: "plain",
  locality: "local",
  max_input_tokens: 32768,
  max_output_tokens: 4096,
  supports_reasoning: false,
  reasoning_toggleable: false,
  supports_tools: false,
  cost_in: null,
  cost_out: null,
  enabled: true,
};

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

describe("Т-502: агентный диалог в ChatPage", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    // Каждый тест стартует с двумя моделями (одна с флагом) — переопределения
    // из других тестов не должны протекать (mockResolvedValue не сбрасывается
    // через clearAllMocks).
    const { apiListAvailableModels } = await import("../api/models");
    vi.mocked(apiListAvailableModels).mockResolvedValue([AGENT_MODEL, PLAIN_MODEL] as any);
  });

  /** Открывает модалку агентного диалога и возвращает её. */
  async function openAgentModal(user: ReturnType<typeof userEvent.setup>) {
    await waitFor(() => {
      expect(screen.getByTestId("new-agent-chat")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("new-agent-chat"));
    return screen.findByRole("dialog");
  }

  /** Полный путь: точка входа → выбор модели → создание диалога. */
  async function createAgentDialog(user: ReturnType<typeof userEvent.setup>) {
    const dialog = await openAgentModal(user);
    await user.click(within(dialog).getByText("local/agent-model"));
    await user.click(within(dialog).getByText("Создать диалог"));
    await waitFor(() => {
      expect(screen.getByText("Новый агентный диалог")).toBeInTheDocument();
    });
  }

  it("кнопка видна при наличии модели с supports_tools", async () => {
    renderChatPage();
    await waitFor(() => {
      expect(screen.getByTestId("new-agent-chat")).toBeInTheDocument();
    });
  });

  it("кнопка скрыта, если ни одна модель не поддерживает инструменты", async () => {
    const { apiListAvailableModels } = await import("../api/models");
    vi.mocked(apiListAvailableModels).mockResolvedValue([PLAIN_MODEL] as any);

    renderChatPage();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Новый диалог" })).toBeInTheDocument();
    });
    expect(screen.queryByTestId("new-agent-chat")).not.toBeInTheDocument();
  });

  it("создание агентного диалога переключает режим", async () => {
    renderChatPage();
    const user = userEvent.setup();

    const dialog = await openAgentModal(user);
    // В модалке только модель с флагом
    expect(within(dialog).queryByText("local/plain-model")).not.toBeInTheDocument();

    await user.click(within(dialog).getByText("local/agent-model"));
    await user.click(within(dialog).getByText("Создать диалог"));

    await waitFor(() => {
      expect(screen.getByText("Новый агентный диалог")).toBeInTheDocument();
    });
  });

  it("отправка в агентном режиме вызывает агентный эндпоинт и показывает шаги", async () => {
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue({
      available: true,
      type: "complete",
      content: "Ответ по документам",
      conversation_id: "conv-agent",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [
        { index: 1, kind: "model", name: null, summary: "Запрошены инструменты", decision: null },
        { index: 2, kind: "tool", name: "search_corpus", summary: "Фрагментов: 1", decision: "allow" },
        { index: 3, kind: "model", name: null, summary: "Финальный ответ", decision: null },
      ],
      sources: [],
      trace_id: "trace-1",
      pending_confirmation: null,
    } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос по корпусу");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    const request = vi.mocked(agentChat).mock.calls[0][0];
    expect(request.model_alias).toBe("local/agent-model");
    expect(request.messages[request.messages.length - 1].content).toBe("Вопрос по корпусу");

    // Ответ и сводка шагов отображаются
    await waitFor(() => {
      expect(screen.getByText("Ответ по документам")).toBeInTheDocument();
      expect(screen.getByTestId("agent-run-summary")).toBeInTheDocument();
      expect(screen.getByText("Фрагментов: 1")).toBeInTheDocument();
    });
  });

  it("деградация: недоступность дополнения показывает причину вместо ввода", async () => {
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue({
      available: false,
      reason: "Агентный модуль недоступен: установите orqion[agent]",
      type: "complete",
      content: "",
      conversation_id: null,
      model: null,
      usage: null,
      steps: [],
      sources: [],
      trace_id: null,
      pending_confirmation: null,
    } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(screen.getByText(/orqion\[agent\]/)).toBeInTheDocument();
    });
  });
});
