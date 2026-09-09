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
  apiStopConversation: vi.fn(),
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

// Т-508: ChatPage запрашивает список скиллов для выбора в агентном режиме.
// Без мока ушёл бы настоящий fetch.
vi.mock("../api/skills", () => ({
  apiListSkills: vi.fn().mockResolvedValue({ skills: [] }),
  apiListAvailableSkills: vi.fn().mockResolvedValue({ skills: [] }),
  apiCreateSkill: vi.fn(),
  apiUpdateSkill: vi.fn(),
  apiDeleteSkill: vi.fn(),
}));

// Т-509: ChatPage запрашивает список профилей для подписи активного профиля.
vi.mock("../api/agent-profiles", () => ({
  apiListAgentProfiles: vi.fn().mockResolvedValue({ profiles: [] }),
  apiListAvailableAgentProfiles: vi.fn().mockResolvedValue({ profiles: [] }),
  apiCreateAgentProfile: vi.fn(),
  apiUpdateAgentProfile: vi.fn(),
  apiDeleteAgentProfile: vi.fn(),
  apiListAgentProfileConversations: vi.fn().mockResolvedValue({
    conversations: [],
    total: 0,
    scope: "own",
  }),
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
    // Скиллов по умолчанию нет: селектор не показывается.
    const { apiListAvailableSkills } = await import("../api/skills");
    vi.mocked(apiListAvailableSkills).mockResolvedValue({ skills: [] } as any);
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

  /** Ответ с запросом подтверждения деструктивного инструмента. */
  function pendingConfirmationResponse() {
    return {
      available: true,
      type: "complete",
      content: "Инструмент запросил подтверждение. Действие не выполнено.",
      conversation_id: "conv-agent",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [
        { index: 1, kind: "model", name: null, summary: "Запрошены инструменты", decision: null },
        {
          index: 2,
          kind: "confirmation",
          name: "demo-build.drop_cache",
          summary: "Ожидает подтверждения пользователя",
          decision: "pending",
        },
      ],
      sources: [],
      trace_id: "trace-1",
      pending_confirmation: {
        call_id: "call-danger",
        tool: "demo-build.drop_cache",
        args: { item: "x" },
      },
    } as any;
  }

  it("пункт 9: запрос подтверждения показывает карточку, одобрение уходит решением", async () => {
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat)
      .mockResolvedValueOnce(pendingConfirmationResponse())
      .mockResolvedValueOnce({
        available: true,
        type: "complete",
        content: "Кэш удалён.",
        conversation_id: "conv-agent",
        model: "local/agent-model",
        usage: { tokens_in: 12, tokens_out: 6 },
        steps: [],
        sources: [],
        trace_id: "trace-2",
        pending_confirmation: null,
      } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Удали кэш");
    await user.click(screen.getByText("Отправить"));

    // Карточка запроса подтверждения с инструментом и параметрами.
    // Имя инструмента проверяется ВНУТРИ карточки: после попутного фикса
    // ленты шагов (Т-508) имя подтверждённого инструмента видно и там —
    // «Подтверждение demo-build.drop_cache» вместо прежнего «Модель»,
    // поэтому глобальный поиск по имени стал неоднозначным.
    await waitFor(() => {
      const card = screen.getByTestId("agent-confirmation-card");
      expect(card).toBeInTheDocument();
      expect(within(card).getByText(/demo-build\.drop_cache/)).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("confirmation-approve"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(2);
    });
    const request = vi.mocked(agentChat).mock.calls[1][0];
    expect(request.confirmation_decision).toBe("approve");
    expect(request.confirmation).toEqual({
      call_id: "call-danger",
      tool: "demo-build.drop_cache",
      args: { item: "x" },
    });
    // Новое сообщение пользователя не добавлялось — тот же буфер.
    const userMsgs = request.messages.filter((m: any) => m.role === "user");
    expect(userMsgs).toHaveLength(1);

    // После исполнения карточка исчезает.
    await waitFor(() => {
      expect(screen.queryByTestId("agent-confirmation-card")).not.toBeInTheDocument();
      expect(screen.getByText("Кэш удалён.")).toBeInTheDocument();
    });
  });

  it("пункт 9: отмена подтверждения уходит решением «отклонить»", async () => {
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat)
      .mockResolvedValueOnce(pendingConfirmationResponse())
      .mockResolvedValueOnce({
        available: true,
        type: "complete",
        content: "Действие отменено. Инструмент не выполнялся.",
        conversation_id: "conv-agent",
        model: "local/agent-model",
        usage: { tokens_in: 0, tokens_out: 0 },
        steps: [
          {
            index: 1,
            kind: "confirmation",
            name: "demo-build.drop_cache",
            summary: "Отменено пользователем",
            decision: "reject",
          },
        ],
        sources: [],
        trace_id: "trace-2",
        pending_confirmation: null,
      } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Удали кэш");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(screen.getByTestId("agent-confirmation-card")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("confirmation-reject"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(2);
    });
    const request = vi.mocked(agentChat).mock.calls[1][0];
    expect(request.confirmation_decision).toBe("reject");

    await waitFor(() => {
      expect(screen.getByText("Действие отменено. Инструмент не выполнялся.")).toBeInTheDocument();
    });
  });

  /** Два скилла в списке выбора. */
  async function seedSkills() {
    const { apiListAvailableSkills } = await import("../api/skills");
    vi.mocked(apiListAvailableSkills).mockResolvedValue({
      skills: [
        { id: "sk-1", name: "Разбор", description: "По документам" },
        { id: "sk-2", name: "Сборка", description: "" },
      ],
    } as any);
  }

  it("Т-508: селектор скилла скрыт, пока скиллов нет", async () => {
    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    expect(screen.queryByTestId("agent-skill-select")).not.toBeInTheDocument();
  });

  it("Т-508: селектор скилла виден в агентном режиме и выбор уходит в запрос", async () => {
    await seedSkills();
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue({
      available: true,
      type: "complete",
      content: "Ответ по скиллу",
      conversation_id: "conv-skill",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [
        { index: 1, kind: "skill", name: "Разбор", summary: "Инструментов в прогоне: 1", decision: null },
        { index: 2, kind: "model", name: null, summary: "Финальный ответ", decision: null },
      ],
      sources: [],
      trace_id: "trace-skill",
      pending_confirmation: null,
      skill_tools_unavailable: [],
    } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);

    const selector = await screen.findByTestId("agent-skill-select");
    await user.selectOptions(selector, "sk-1");

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    const request = vi.mocked(agentChat).mock.calls[0][0];
    expect(request.skill_id).toBe("sk-1");

    // Шаг скилла подписан «Скилл», а не «Модель» (попутный фикс ленты).
    await waitFor(() => {
      expect(screen.getByTestId("agent-run-summary")).toBeInTheDocument();
      expect(screen.getByText("Скилл Разбор")).toBeInTheDocument();
      expect(screen.getByText(/Инструментов в прогоне: 1/)).toBeInTheDocument();
    });
  });

  it("Т-508: без выбора скилла в запрос уходит null", async () => {
    await seedSkills();
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue({
      available: true,
      type: "complete",
      content: "Обычный ответ",
      conversation_id: "conv-plain",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [],
      sources: [],
      trace_id: "trace-plain",
      pending_confirmation: null,
      skill_tools_unavailable: [],
    } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);
    await screen.findByTestId("agent-skill-select");

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(agentChat).mock.calls[0][0].skill_id).toBeNull();
  });

  it("Т-508: недоступные инструменты скилла показаны явно", async () => {
    await seedSkills();
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue({
      available: true,
      type: "complete",
      content: "Ответ без части инструментов",
      conversation_id: "conv-skill",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [
        { index: 1, kind: "skill", name: "Разбор", summary: "Инструментов в прогоне: 1", decision: null },
      ],
      sources: [],
      trace_id: "trace-skill",
      pending_confirmation: null,
      skill_tools_unavailable: ["wiki.lookup", "demo.echo"],
    } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);
    await user.selectOptions(await screen.findByTestId("agent-skill-select"), "sk-1");

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Вопрос");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      const banner = screen.getByTestId("agent-skill-tools-unavailable");
      expect(banner).toHaveTextContent("wiki.lookup");
      expect(banner).toHaveTextContent("demo.echo");
    });
  });

  it("Т-508: решение по подтверждению уходит с тем же скиллом", async () => {
    await seedSkills();
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat)
      .mockResolvedValueOnce({
        ...pendingConfirmationResponse(),
        skill_tools_unavailable: [],
      })
      .mockResolvedValueOnce({
        available: true,
        type: "complete",
        content: "Кэш удалён.",
        conversation_id: "conv-agent",
        model: "local/agent-model",
        usage: { tokens_in: 12, tokens_out: 6 },
        steps: [],
        sources: [],
        trace_id: "trace-2",
        pending_confirmation: null,
        skill_tools_unavailable: [],
      } as any);

    renderChatPage();
    const user = userEvent.setup();
    await createAgentDialog(user);
    await user.selectOptions(await screen.findByTestId("agent-skill-select"), "sk-2");

    await user.type(screen.getByPlaceholderText(/Введите сообщение/), "Удали кэш");
    await user.click(screen.getByText("Отправить"));

    await waitFor(() => {
      expect(screen.getByTestId("agent-confirmation-card")).toBeInTheDocument();
    });
    // Пока ждёт решения, сменить скилл нельзя: иначе одобряемый инструмент
    // мог бы исчезнуть из прогона.
    expect(screen.getByTestId("agent-skill-select")).toBeDisabled();

    await user.click(screen.getByTestId("confirmation-approve"));

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(2);
    });
    const request = vi.mocked(agentChat).mock.calls[1][0];
    expect(request.skill_id).toBe("sk-2");
    expect(request.confirmation_decision).toBe("approve");
  });
});

describe("Т-509: профиль агента и остановка прогона в ChatPage", () => {
  const PROFILE = { id: "p1", name: "Аналитик", description: "Отчёты по корпусу" };

  /** Агентный диалог, уже созданный от профиля. */
  const PROFILE_CONVERSATION = {
    id: "conv-profile",
    title: "Профильный диалог",
    archived: false,
    mode: "agent",
    agent_profile_id: "p1",
    stop_requested: false,
    created_at: "2026-09-09T10:00:00Z",
    message_count: 0,
    context_reset_at: null,
    messages: [],
  };

  function completeResponse(overrides: Record<string, unknown> = {}) {
    return {
      available: true,
      type: "complete",
      content: "Ответ по профилю",
      conversation_id: "conv-profile",
      model: "local/agent-model",
      usage: { tokens_in: 10, tokens_out: 5 },
      steps: [],
      sources: [],
      trace_id: "trace-profile",
      pending_confirmation: null,
      skill_tools_unavailable: [],
      ...overrides,
    } as any;
  }

  async function seedProfiles(profiles = [PROFILE]) {
    const { apiListAvailableAgentProfiles } = await import("../api/agent-profiles");
    vi.mocked(apiListAvailableAgentProfiles).mockResolvedValue({ profiles } as any);
  }

  async function seedSkills() {
    const { apiListAvailableSkills } = await import("../api/skills");
    vi.mocked(apiListAvailableSkills).mockResolvedValue({
      skills: [{ id: "sk-1", name: "Разбор", description: "" }],
    } as any);
  }

  function renderChatPageWith(agentStart: { profileId: string | null; nonce: number }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <ChatPage agentStart={agentStart} />
      </QueryClientProvider>,
    );
    return client;
  }

  /**
   * Вводит текст и ждёт, пока кнопка отправки станет активной, затем жмёт её.
   *
   * Порядок существенен: кнопка неактивна при пустом поле ввода, поэтому
   * ждать готовности до ввода бессмысленно. Ожидание после ввода покрывает
   * и асинхронную загрузку списка моделей.
   */
  async function typeAndSend(user: ReturnType<typeof userEvent.setup>, text: string) {
    await user.type(screen.getByPlaceholderText(/Введите сообщение/), text);
    await waitFor(() => {
      expect(screen.getByText("Отправить")).not.toBeDisabled();
    });
    await user.click(screen.getByText("Отправить"));
  }

  beforeEach(async () => {
    vi.clearAllMocks();
    const { apiListAvailableModels } = await import("../api/models");
    vi.mocked(apiListAvailableModels).mockResolvedValue([AGENT_MODEL, PLAIN_MODEL] as any);
    const { apiListConversations } = await import("../api/conversations");
    vi.mocked(apiListConversations).mockResolvedValue({ conversations: [], total: 0 } as any);
    await seedProfiles([]);
    await seedSkills();
  });

  it("решение 2: профиль фиксирует модель и скилл — селекторы скрыты, видна подпись", async () => {
    await seedProfiles();
    renderChatPageWith({ profileId: "p1", nonce: 1 });

    await waitFor(() => {
      expect(screen.getByTestId("agent-profile-badge")).toHaveTextContent("Аналитик");
    });
    // Селектора скилла нет, хотя скиллы в списке выбора есть.
    expect(screen.queryByTestId("agent-skill-select")).not.toBeInTheDocument();
    // Селектора модели нет: модель задаёт профиль.
    expect(screen.queryByRole("option", { name: /local\/agent-model/ })).not.toBeInTheDocument();
  });

  it("решение 2: прогон от профиля шлёт agent_profile_id и не шлёт model_alias/skill_id", async () => {
    await seedProfiles();
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue(completeResponse());

    renderChatPageWith({ profileId: "p1", nonce: 1 });
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("agent-profile-badge")).toBeInTheDocument();
    });
    await typeAndSend(user, "Вопрос по корпусу");

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    const request = vi.mocked(agentChat).mock.calls[0][0];
    expect(request.agent_profile_id).toBe("p1");
    // Переопределение конфигурации профиля сервер отклоняет (400), поэтому
    // клиент эти поля не отправляет вовсе.
    expect(request.model_alias).toBeNull();
    expect(request.skill_id).toBeNull();
    await waitFor(() => {
      expect(screen.getByText("Ответ по профилю")).toBeInTheDocument();
    });
  });

  it("решение 2: диалог, созданный от профиля, продолжает его — профиль берётся с диалога", async () => {
    await seedProfiles();
    const { apiListConversations, apiGetConversation } = await import("../api/conversations");
    vi.mocked(apiListConversations).mockResolvedValue({
      conversations: [PROFILE_CONVERSATION],
      total: 1,
    } as any);
    vi.mocked(apiGetConversation).mockResolvedValue(PROFILE_CONVERSATION as any);
    const { agentChat } = await import("../api/agent");
    vi.mocked(agentChat).mockResolvedValue(completeResponse());

    renderChatPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Профильный диалог"));
    await waitFor(() => {
      expect(screen.getByTestId("agent-profile-badge")).toHaveTextContent("Аналитик");
    });

    await typeAndSend(user, "Продолжаем");

    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    const request = vi.mocked(agentChat).mock.calls[0][0];
    expect(request.agent_profile_id).toBe("p1");
    expect(request.conversation_id).toBe("conv-profile");
    expect(request.model_alias).toBeNull();
  });

  it("решение 9: старт без профиля открывает выбор модели — ad-hoc путь сохранён", async () => {
    renderChatPageWith({ profileId: null, nonce: 1 });

    const dialog = await screen.findByRole("dialog");
    // Модалка выбора модели: профиль не отобрал ручной путь. Ждём появления
    // модели — список приходит асинхронно после открытия модалки.
    expect(await within(dialog).findByText("local/agent-model")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-profile-badge")).not.toBeInTheDocument();
  });

  it("решение 7: остановка во время прогона ставит флаг на сервере", async () => {
    await seedProfiles();
    const { apiListConversations, apiGetConversation, apiStopConversation } = await import(
      "../api/conversations"
    );
    vi.mocked(apiListConversations).mockResolvedValue({
      conversations: [PROFILE_CONVERSATION],
      total: 1,
    } as any);
    vi.mocked(apiGetConversation).mockResolvedValue(PROFILE_CONVERSATION as any);
    vi.mocked(apiStopConversation).mockResolvedValue({
      ...PROFILE_CONVERSATION,
      stop_requested: true,
    } as any);

    // Прогон не завершается, пока тест не разрешит промис: так кнопка
    // остановки видна именно во время прогона.
    const { agentChat } = await import("../api/agent");
    let finishRun: (value: unknown) => void = () => {};
    vi.mocked(agentChat).mockReturnValue(
      new Promise((resolve) => {
        finishRun = resolve;
      }) as any,
    );

    renderChatPage();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Профильный диалог"));
    await typeAndSend(user, "Долгий вопрос");

    const stop = await screen.findByTestId("agent-stop");
    await user.click(stop);

    await waitFor(() => {
      expect(apiStopConversation).toHaveBeenCalledWith("conv-profile");
    });

    finishRun(
      completeResponse({
        type: "stopped",
        content: "Прогон остановлен по запросу пользователя: текущий шаг доработан, следующий не начинался.",
        steps: [
          { index: 1, kind: "model", name: null, summary: "Запрошены инструменты", decision: null },
          { index: 2, kind: "stop", name: null, summary: "Прогон остановлен по запросу пользователя", decision: null },
        ],
      }),
    );

    // Остановка — штатное завершение: ответ и лента шагов показаны, ошибки нет.
    await waitFor(() => {
      expect(screen.getByTestId("agent-run-summary")).toBeInTheDocument();
    });
    expect(screen.getByTestId("agent-run-summary")).toHaveTextContent("Остановка");
    // Текст ответа совпадает с подписью шага остановки, поэтому проверяется
    // наличие, а не единственность совпадения.
    expect(
      screen.getAllByText(/Прогон остановлен по запросу пользователя/).length,
    ).toBeGreaterThan(0);
    // Ошибка не показана: остановка — не сбой, расход выполненных шагов сохранён.
    expect(screen.queryByText("Ошибка:")).not.toBeInTheDocument();
  });

  it("решение 7: для первого сообщения кнопки остановки нет — диалога на сервере ещё нет", async () => {
    await seedProfiles();
    const { agentChat } = await import("../api/agent");
    let finishRun: (value: unknown) => void = () => {};
    vi.mocked(agentChat).mockReturnValue(
      new Promise((resolve) => {
        finishRun = resolve;
      }) as any,
    );

    renderChatPageWith({ profileId: "p1", nonce: 1 });
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByTestId("agent-profile-badge")).toBeInTheDocument();
    });
    await typeAndSend(user, "Первый вопрос");

    // Прогон идёт, но идентификатор диалога появится только в ответе:
    // ставить флаг остановки ещё не на что.
    await waitFor(() => {
      expect(agentChat).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByTestId("agent-stop")).not.toBeInTheDocument();

    finishRun(completeResponse());
    await waitFor(() => {
      expect(screen.getByText("Ответ по профилю")).toBeInTheDocument();
    });
  });
});
