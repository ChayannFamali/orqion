/**
 * Т-508: админский каталог скиллов.
 *
 * Проверяются решение 4 (каталог по способности, полные поля включая
 * выключенные), решение 6 (пустой набор инструментов показывается явной
 * подписью — скилл не даёт доступа ни к одному инструменту) и решение 7
 * (список, создание, правка, включение/выключение, удаление).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SkillsPage } from "../pages/SkillsPage";
import {
  useSkills,
  useCreateSkill,
  useUpdateSkill,
  useDeleteSkill,
} from "../hooks/useSkills";
import type { SkillListResponse } from "../api/types";

vi.mock("../hooks/useSkills");

function makeSkill(overrides: Partial<SkillListResponse["skills"][0]> = {}) {
  return {
    id: "sk-1",
    name: "Разбор",
    description: "Для типового сценария",
    prompt_text: "Отвечай по документам",
    tools: ["search_corpus"],
    default_max_tokens: null,
    enabled: true,
    created_at: "2026-09-05T00:00:00Z",
    ...overrides,
  };
}

function mockList(skills: SkillListResponse["skills"]): SkillListResponse {
  return { skills };
}

function mockHooks(list: SkillListResponse["skills"] | undefined) {
  vi.mocked(useSkills).mockReturnValue({
    data: list === undefined ? undefined : mockList(list),
    isLoading: list === undefined,
    error: null,
  } as ReturnType<typeof useSkills>);
  vi.mocked(useCreateSkill).mockReturnValue({} as ReturnType<typeof useCreateSkill>);
  vi.mocked(useUpdateSkill).mockReturnValue({} as ReturnType<typeof useUpdateSkill>);
  vi.mocked(useDeleteSkill).mockReturnValue({} as ReturnType<typeof useDeleteSkill>);
}

const EMPTY_TOOLS_HINT =
  "Инструменты не выбраны: скилл не даёт агенту доступа ни к одному инструменту";

describe("SkillsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("показывает список с именем, инструментами и статусом", () => {
    mockHooks([
      makeSkill({ id: "s1", name: "Разбор", tools: ["search_corpus", "demo.echo"] }),
      makeSkill({
        id: "s2",
        name: "Сборка",
        tools: [],
        enabled: false,
        description: "",
      }),
    ]);

    render(<SkillsPage />);

    expect(screen.getByTestId("skill-Разбор")).toBeInTheDocument();
    expect(screen.getByTestId("skill-tools-Разбор")).toHaveTextContent(
      "search_corpus, demo.echo",
    );
    expect(screen.getByText("включён")).toBeInTheDocument();
    expect(screen.getByTestId("skill-Сборка")).toBeInTheDocument();
    expect(screen.getByText("отключён")).toBeInTheDocument();
  });

  it("решение 6: пустой набор инструментов показан явной подписью", () => {
    mockHooks([makeSkill({ tools: [] })]);

    render(<SkillsPage />);

    // Формулировка подписи — дословно из карточки (правка пользователя):
    // админ должен видеть, что скилл не даёт доступа ни к чему.
    expect(screen.getByTestId("skill-tools-Разбор")).toHaveTextContent(EMPTY_TOOLS_HINT);
    expect(screen.getByText(EMPTY_TOOLS_HINT)).toBeInTheDocument();
    // Перечня инструментов при этом нет.
    expect(screen.getByTestId("skill-tools-Разбор")).not.toHaveTextContent(
      "Инструменты:",
    );
  });

  it("показывает пустое состояние, загрузку и ошибку", () => {
    mockHooks([]);
    const { unmount } = render(<SkillsPage />);
    expect(screen.getByTestId("skills-empty")).toBeInTheDocument();
    unmount();

    vi.mocked(useSkills).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useSkills>);
    const loading = render(<SkillsPage />);
    expect(screen.getByTestId("skills-loading")).toBeInTheDocument();
    loading.unmount();

    vi.mocked(useSkills).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new Error("boom"),
    } as ReturnType<typeof useSkills>);
    render(<SkillsPage />);
    expect(screen.getByTestId("skills-error")).toBeInTheDocument();
  });

  it("создание: инструменты из текста построчно, пустой список допустим", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeSkill());
    mockHooks([]);
    vi.mocked(useCreateSkill).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateSkill>);

    render(<SkillsPage />);

    fireEvent.click(screen.getByTestId("skills-add"));

    // До заполнения — явная подпись об отсутствии инструментов.
    expect(screen.getByTestId("skill-form-tools-warning")).toHaveTextContent(EMPTY_TOOLS_HINT);

    fireEvent.change(screen.getByTestId("skill-form-name"), {
      target: { value: "Разбор" },
    });
    fireEvent.change(screen.getByTestId("skill-form-prompt"), {
      target: { value: "Отвечай по документам" },
    });
    fireEvent.change(screen.getByTestId("skill-form-tools"), {
      target: { value: "search_corpus\n\ndemo.echo\n" },
    });
    fireEvent.change(screen.getByTestId("skill-form-max-tokens"), {
      target: { value: "512" },
    });

    // После заполнения подписи-предупреждения нет, есть счётчик.
    expect(screen.queryByTestId("skill-form-tools-warning")).not.toBeInTheDocument();
    expect(screen.getByText("Выбрано инструментов: 2")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("skill-create-submit"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        name: "Разбор",
        description: "",
        prompt_text: "Отвечай по документам",
        tools: ["search_corpus", "demo.echo"],
        default_max_tokens: 512,
        enabled: true,
      });
    });
  });

  it("решение 3: в форме нет признака «доверенности» инструментов", () => {
    mockHooks([makeSkill()]);

    render(<SkillsPage />);
    fireEvent.click(screen.getByTestId("skills-add"));

    // Никаких полей автоодобрения деструктивных действий.
    expect(screen.queryByTestId(/auto.?approve/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/автоодобр/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/доверенн/i)).not.toBeInTheDocument();
  });

  it("переключение статуса шлёт полную замену полей с новым enabled", () => {
    const mutate = vi.fn();
    mockHooks([makeSkill({ id: "s1", name: "Разбор", enabled: true })]);
    vi.mocked(useUpdateSkill).mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateSkill>);

    render(<SkillsPage />);

    fireEvent.click(screen.getByTestId("skill-toggle-Разбор"));

    expect(mutate).toHaveBeenCalledWith({
      skillId: "s1",
      body: {
        name: "Разбор",
        description: "Для типового сценария",
        prompt_text: "Отвечай по документам",
        tools: ["search_corpus"],
        default_max_tokens: null,
        enabled: false,
      },
    });
    // Служебные поля каталога в запрос не попадают: схема их отвергает.
    const body = mutate.mock.calls[0][0].body as Record<string, unknown>;
    expect(body).not.toHaveProperty("id");
    expect(body).not.toHaveProperty("created_at");
  });

  it("выключенный скилл можно включить обратно из каталога", () => {
    const mutate = vi.fn();
    mockHooks([makeSkill({ id: "s1", name: "Разбор", enabled: false })]);
    vi.mocked(useUpdateSkill).mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateSkill>);

    render(<SkillsPage />);

    // Каталог показывает выключенные — иначе включить обратно нечем.
    expect(screen.getByTestId("skill-Разбор")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("skill-toggle-Разбор"));

    const body = mutate.mock.calls[0][0].body;
    expect(body.enabled).toBe(true);
  });

  it("правка сохраняет имя, инструкции и инструменты", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(makeSkill());
    mockHooks([makeSkill({ id: "s1", name: "Разбор" })]);
    vi.mocked(useUpdateSkill).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateSkill>);

    render(<SkillsPage />);

    fireEvent.click(screen.getByTestId("skill-edit-Разбор"));
    fireEvent.change(screen.getByTestId("skill-form-name"), {
      target: { value: "Разбор v2" },
    });
    fireEvent.change(screen.getByTestId("skill-form-tools"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("skill-edit-submit"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({
        skillId: "s1",
        body: {
          name: "Разбор v2",
          description: "Для типового сценария",
          prompt_text: "Отвечай по документам",
          tools: [],
          default_max_tokens: null,
          enabled: true,
        },
      });
    });
  });

  it("удаление требует подтверждения в модалке", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ deleted: true });
    mockHooks([makeSkill({ id: "s1", name: "Разбор" })]);
    vi.mocked(useDeleteSkill).mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteSkill>);

    render(<SkillsPage />);

    fireEvent.click(screen.getByTestId("skill-delete-Разбор"));
    // До подтверждения удаления не было.
    expect(mutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(/Диалоги, в которых скилл использовался, сохранятся/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("skill-delete-confirm"));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("s1");
    });
  });
});
