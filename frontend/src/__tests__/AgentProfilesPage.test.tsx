/**
 * Т-509: раздел «Агенты» — каталог профилей и точка входа для старта диалога.
 *
 * Проверяются решение 1 (профиль — модель + скилл: полей промпта,
 * инструментов и «доверенности» в форме нет; модель выбирается только из
 * пригодных для агентного режима), решение 3 (раздел виден всем, управление
 * — только со способностью ``manage_agents``; каталог без права не
 * запрашивается вовсе), решение 5 (drill-down показывает метаданные и
 * расход, чужой заголовок скрыт, содержимого переписки нет), решение 8
 * (удаление объясняет блокировку при наличии диалогов) и решение 9
 * (кнопка «Создать без профиля» сохраняет ad-hoc путь).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AgentProfilesPage } from "../pages/AgentProfilesPage";
import {
  useAgentProfiles,
  useAgentProfileConversations,
  useAvailableAgentProfiles,
  useCreateAgentProfile,
  useDeleteAgentProfile,
  useUpdateAgentProfile,
} from "../hooks/useAgentProfiles";
import { useEnabledModels } from "../hooks/useModels";
import { useAvailableSkills } from "../hooks/useSkills";
import { useCurrentUser } from "../hooks/useAuth";

vi.mock("../hooks/useAgentProfiles");
vi.mock("../hooks/useModels");
vi.mock("../hooks/useSkills");
vi.mock("../hooks/useAuth");

function makeProfile(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    name: "Аналитик",
    description: "Отчёты по корпусу",
    model_id: "m1",
    model_alias: "local/agent-model",
    skill_id: "sk-1",
    skill_name: "Разбор",
    enabled: true,
    created_at: "2026-09-09T00:00:00Z",
    ...overrides,
  };
}

function makeModel(id: string, alias: string, supportsTools: boolean) {
  return {
    id,
    alias,
    upstream_name: alias,
    locality: "local",
    max_input_tokens: 32768,
    max_output_tokens: 4096,
    supports_reasoning: false,
    reasoning_toggleable: false,
    supports_tools: supportsTools,
    cost_in: null,
    cost_out: null,
    enabled: true,
  };
}

/**
 * @param capabilities способности пользователя; ``["*"]`` — администратор
 * @param catalog профили админского каталога (``undefined`` — идёт загрузка)
 * @param available профили списка выбора
 */
function mockHooks({
  capabilities = ["*"],
  catalog,
  available = [],
}: {
  capabilities?: string[];
  catalog?: Record<string, unknown>[];
  available?: { id: string; name: string; description: string }[];
} = {}) {
  vi.mocked(useCurrentUser).mockReturnValue({
    data: { id: "u1", email: "admin@orqion.local", capabilities },
  } as ReturnType<typeof useCurrentUser>);
  vi.mocked(useAgentProfiles).mockReturnValue({
    data: catalog === undefined ? undefined : { profiles: catalog },
    isLoading: catalog === undefined,
    error: null,
  } as ReturnType<typeof useAgentProfiles>);
  vi.mocked(useAvailableAgentProfiles).mockReturnValue({
    data: { profiles: available },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useAvailableAgentProfiles>);
  vi.mocked(useAgentProfileConversations).mockReturnValue({
    data: { conversations: [], total: 0, scope: "own" },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useAgentProfileConversations>);
  vi.mocked(useCreateAgentProfile).mockReturnValue(
    {} as ReturnType<typeof useCreateAgentProfile>,
  );
  vi.mocked(useUpdateAgentProfile).mockReturnValue(
    {} as ReturnType<typeof useUpdateAgentProfile>,
  );
  vi.mocked(useDeleteAgentProfile).mockReturnValue(
    {} as ReturnType<typeof useDeleteAgentProfile>,
  );
  vi.mocked(useEnabledModels).mockReturnValue({
    data: [makeModel("m1", "local/agent-model", true), makeModel("m2", "local/plain", false)],
  } as ReturnType<typeof useEnabledModels>);
  vi.mocked(useAvailableSkills).mockReturnValue({
    data: { skills: [{ id: "sk-1", name: "Разбор", description: "" }] },
  } as ReturnType<typeof useAvailableSkills>);
}

describe("AgentProfilesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("решение 3: со способностью manage_agents видны каталог и кнопки управления", () => {
    mockHooks({ capabilities: ["*"], catalog: [makeProfile()] });

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);

    expect(screen.getByTestId("agent-profile-Аналитик")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profiles-add")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-edit-Аналитик")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-delete-Аналитик")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-drilldown-Аналитик")).toBeInTheDocument();
    // Модель и скилл показаны явно: профиль — это именно они (решение 1).
    expect(screen.getByTestId("agent-profile-model-Аналитик")).toHaveTextContent(
      "local/agent-model",
    );
    expect(screen.getByTestId("agent-profile-model-Аналитик")).toHaveTextContent("Разбор");
  });

  it("решение 3: без manage_agents каталог не запрашивается, управления нет", () => {
    mockHooks({
      capabilities: ["chat"],
      // Каталог «загружается» — если бы раздел его запросил, тест завис бы
      // на индикаторе загрузки вместо списка выбора.
      catalog: undefined,
      available: [{ id: "p1", name: "Аналитик", description: "Отчёты" }],
    });

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);

    // Условный запрос не уходит без права: иначе 404 на пустом месте.
    expect(useAgentProfiles).toHaveBeenCalledWith(false);
    expect(screen.getByTestId("agent-profile-available-Аналитик")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-profiles-add")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-profile-edit-Аналитик")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-profile-delete-Аналитик")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-profile-toggle-Аналитик")).not.toBeInTheDocument();
    // Drill-down при этом доступен: без права он показывает только свои
    // диалоги, и точка входа обязана быть (решение 5).
    expect(screen.getByTestId("agent-profile-drilldown-Аналитик")).toHaveTextContent(
      "Мои диалоги",
    );
  });

  it("решение 3: раздел виден и без права — точка входа для старта диалога", () => {
    mockHooks({
      capabilities: ["chat"],
      catalog: undefined,
      available: [{ id: "p1", name: "Аналитик", description: "" }],
    });

    const onStartDialog = vi.fn();
    render(<AgentProfilesPage onStartDialog={onStartDialog} />);

    fireEvent.click(screen.getByTestId("agent-profile-start-Аналитик"));
    expect(onStartDialog).toHaveBeenCalledWith("p1");
  });

  it("решение 9: «Создать без профиля» сохраняет ad-hoc путь", () => {
    mockHooks({ capabilities: ["chat"], catalog: undefined, available: [] });

    const onStartDialog = vi.fn();
    render(<AgentProfilesPage onStartDialog={onStartDialog} />);

    fireEvent.click(screen.getByTestId("agent-start-adhoc"));
    expect(onStartDialog).toHaveBeenCalledWith(null);
  });

  it("решение 1: в форме нет промпта, инструментов и признака доверенности", () => {
    mockHooks({ capabilities: ["*"], catalog: [] });

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profiles-add"));

    expect(screen.getByTestId("agent-profile-form-name")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-form-model")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-form-skill")).toBeInTheDocument();
    // Промпт и инструменты задаёт скилл (Т-508), а не профиль.
    expect(screen.queryByTestId(/prompt/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId(/tools/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/автоодобр/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/доверенн/i)).not.toBeInTheDocument();
  });

  it("решение 1: модель выбирается только из пригодных для агентного режима", () => {
    mockHooks({ capabilities: ["*"], catalog: [] });

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profiles-add"));

    const select = screen.getByTestId("agent-profile-form-model");
    expect(select).toContainElement(screen.getByRole("option", { name: "local/agent-model" }));
    // Модель без флага инструментов в списке отсутствует: профиль с ней
    // сервер отвергает на создании (400).
    expect(select.querySelector('option[value="m2"]')).toBeNull();
    // Без выбранной модели создание недоступно.
    expect(screen.getByTestId("agent-profile-create-submit")).toBeDisabled();
  });

  it("создание профиля отправляет модель и скилл без служебных полей", async () => {
    mockHooks({ capabilities: ["*"], catalog: [] });
    const mutateAsync = vi.fn().mockResolvedValue(makeProfile());
    vi.mocked(useCreateAgentProfile).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgentProfile>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profiles-add"));
    fireEvent.change(screen.getByTestId("agent-profile-form-name"), {
      target: { value: "Аналитик" },
    });
    fireEvent.change(screen.getByTestId("agent-profile-form-model"), {
      target: { value: "m1" },
    });
    fireEvent.change(screen.getByTestId("agent-profile-form-skill"), {
      target: { value: "sk-1" },
    });
    fireEvent.click(screen.getByTestId("agent-profile-create-submit"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        name: "Аналитик",
        description: "",
        model_id: "m1",
        skill_id: "sk-1",
        enabled: true,
      });
    });
  });

  it("профиль без скилла создаётся: пустой выбор уходит как null", async () => {
    mockHooks({ capabilities: ["*"], catalog: [] });
    const mutateAsync = vi.fn().mockResolvedValue(makeProfile({ skill_id: null }));
    vi.mocked(useCreateAgentProfile).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateAgentProfile>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profiles-add"));
    fireEvent.change(screen.getByTestId("agent-profile-form-name"), {
      target: { value: "Без скилла" },
    });
    fireEvent.change(screen.getByTestId("agent-profile-form-model"), {
      target: { value: "m1" },
    });
    fireEvent.click(screen.getByTestId("agent-profile-create-submit"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        name: "Без скилла",
        description: "",
        model_id: "m1",
        skill_id: null,
        enabled: true,
      });
    });
  });

  it("переключение статуса шлёт полную замену полей с новым enabled", () => {
    mockHooks({ capabilities: ["*"], catalog: [makeProfile({ enabled: true })] });
    const mutate = vi.fn();
    vi.mocked(useUpdateAgentProfile).mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateAgentProfile>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profile-toggle-Аналитик"));

    expect(mutate).toHaveBeenCalledWith({
      profileId: "p1",
      body: {
        name: "Аналитик",
        description: "Отчёты по корпусу",
        model_id: "m1",
        skill_id: "sk-1",
        enabled: false,
      },
    });
    // Служебные поля каталога в запрос не попадают: схема их отвергает.
    const body = mutate.mock.calls[0][0].body as Record<string, unknown>;
    expect(body).not.toHaveProperty("id");
    expect(body).not.toHaveProperty("created_at");
    expect(body).not.toHaveProperty("model_alias");
    expect(body).not.toHaveProperty("skill_name");
  });

  it("отключённый профиль показан в каталоге, но старт диалога от него недоступен", () => {
    mockHooks({ capabilities: ["*"], catalog: [makeProfile({ enabled: false })] });

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);

    // Каталог показывает отключённые — иначе включить обратно нечем.
    expect(screen.getByText("отключён")).toBeInTheDocument();
    expect(screen.getByTestId("agent-profile-toggle-Аналитик")).toHaveTextContent("Включить");
    // Прогон от отключённого профиля сервер отклоняет (400), поэтому кнопки
    // старта нет: интерфейс не предлагает заведомо нерабочее действие.
    expect(screen.queryByTestId("agent-profile-start-Аналитик")).not.toBeInTheDocument();
  });

  it("решение 8: модалка удаления объясняет блокировку при наличии диалогов", async () => {
    mockHooks({ capabilities: ["*"], catalog: [makeProfile()] });
    const mutateAsync = vi.fn().mockResolvedValue({ deleted: true });
    vi.mocked(useDeleteAgentProfile).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteAgentProfile>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profile-delete-Аналитик"));

    expect(mutateAsync).not.toHaveBeenCalled();
    expect(
      screen.getByText(/Удаление заблокировано, если у профиля есть диалоги/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agent-profile-delete-confirm"));
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("p1");
    });
  });

  it("решение 5: drill-down показывает метаданные и расход, чужой заголовок скрыт", () => {
    mockHooks({ capabilities: ["*"], catalog: [makeProfile()] });
    vi.mocked(useAgentProfileConversations).mockReturnValue({
      data: {
        conversations: [
          {
            id: "conv-own-1234",
            user_id: "u1",
            title: "Мой диалог",
            archived: false,
            created_at: "2026-09-09T10:00:00Z",
            last_activity_at: "2026-09-09T10:05:00Z",
            requests: 3,
            tokens_in: 120,
            tokens_out: 80,
            cost: 0,
          },
          {
            id: "conv-other-5678",
            user_id: "u2",
            title: null,
            archived: true,
            created_at: "2026-09-09T11:00:00Z",
            last_activity_at: "2026-09-09T11:20:00Z",
            requests: 1,
            tokens_in: 10,
            tokens_out: 5,
            cost: 0,
          },
        ],
        total: 2,
        scope: "workspace",
      },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useAgentProfileConversations>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profile-drilldown-Аналитик"));

    const table = screen.getByTestId("agent-profile-drilldown-table");
    expect(screen.getByTestId("agent-profile-drilldown-scope")).toHaveTextContent(
      "Все диалоги рабочей области",
    );
    // Расход — метаданные из существующего агрегата.
    expect(table).toHaveTextContent("Мой диалог");
    expect(table).toHaveTextContent("200");
    // Чужой заголовок (производное переписки) не отдаётся и не показывается.
    expect(table).toHaveTextContent("(заголовок скрыт)");
    expect(table).toHaveTextContent("архив");
  });

  it("решение 5: без права управления drill-down открывается из списка выбора и показывает свои диалоги", () => {
    mockHooks({
      capabilities: ["chat"],
      catalog: undefined,
      available: [{ id: "p1", name: "Аналитик", description: "" }],
    });
    vi.mocked(useAgentProfileConversations).mockReturnValue({
      data: {
        conversations: [
          {
            id: "conv-own-1234",
            user_id: "u1",
            title: "Мой диалог",
            archived: false,
            created_at: "2026-09-09T10:00:00Z",
            last_activity_at: "2026-09-09T10:05:00Z",
            requests: 1,
            tokens_in: 5,
            tokens_out: 3,
            cost: 0,
          },
        ],
        total: 1,
        scope: "own",
      },
      isLoading: false,
      error: null,
    } as ReturnType<typeof useAgentProfileConversations>);

    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    fireEvent.click(screen.getByTestId("agent-profile-drilldown-Аналитик"));

    expect(screen.getByTestId("agent-profile-drilldown-scope")).toHaveTextContent(
      "Ваши диалоги этого профиля",
    );
    expect(screen.getByTestId("agent-profile-drilldown-table")).toHaveTextContent(
      "Мой диалог",
    );
  });

  it("показывает загрузку и ошибку каталога", () => {
    mockHooks({ capabilities: ["*"], catalog: undefined });
    const loading = render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    expect(screen.getByTestId("agent-profiles-loading")).toBeInTheDocument();
    loading.unmount();

    vi.mocked(useCurrentUser).mockReturnValue({
      data: { id: "u1", email: "admin@orqion.local", capabilities: ["*"] },
    } as ReturnType<typeof useCurrentUser>);
    vi.mocked(useAgentProfiles).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
    } as ReturnType<typeof useAgentProfiles>);
    render(<AgentProfilesPage onStartDialog={vi.fn()} />);
    expect(screen.getByTestId("agent-profiles-error")).toBeInTheDocument();
  });
});
