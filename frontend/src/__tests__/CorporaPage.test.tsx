import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CorporaPage } from "../pages/CorporaPage";
import { useCorpora, useCreateCorpus, useUpdateCorpus } from "../hooks/useCorpora";
import type { CorpusListResponse } from "../api/types";

vi.mock("../hooks/useCorpora");
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function makeCorpus(overrides: Partial<CorpusListResponse["corpora"][0]> = {}) {
  return {
    id: "c1",
    name: "public",
    data_class: "К0",
    pinned_model_id: null,
    active_index_version_id: null,
    ...overrides,
  };
}

const mockMutateAsync = vi.fn();

function mockHooks(
  corporaData?: CorpusListResponse,
  error: unknown = null,
) {
  vi.mocked(useCorpora).mockReturnValue({
    data: corporaData,
    isLoading: false,
    error,
  } as ReturnType<typeof useCorpora>);
  vi.mocked(useCreateCorpus).mockReturnValue({
    isPending: false,
  } as ReturnType<typeof useCreateCorpus>);
  vi.mocked(useUpdateCorpus).mockReturnValue({
    isPending: false,
    mutateAsync: mockMutateAsync,
  } as ReturnType<typeof useUpdateCorpus>);
}

describe("CorporaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders corpus list with name and data_class badge", () => {
    mockHooks({
      corpora: [
        makeCorpus({ id: "c1", name: "public", data_class: "К0" }),
        makeCorpus({ id: "c2", name: "internal", data_class: "К2" }),
      ],
    });

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByText("public")).toBeInTheDocument();
    expect(screen.getByText("internal")).toBeInTheDocument();
    expect(screen.getByText("К0")).toBeInTheDocument();
    expect(screen.getByText("К2")).toBeInTheDocument();
  });

  it("shows empty state when no corpora", () => {
    mockHooks({ corpora: [] });

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByText("Нет корпусов")).toBeInTheDocument();
  });

  it("shows error state on failure", () => {
    mockHooks(undefined, new Error("fetch failed"));

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByText("Ошибка загрузки корпусов")).toBeInTheDocument();
  });

  it("shows loading spinner", () => {
    vi.mocked(useCorpora).mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    } as ReturnType<typeof useCorpora>);
    vi.mocked(useCreateCorpus).mockReturnValue({} as ReturnType<typeof useCreateCorpus>);

    const { container } = render(<CorporaPage capabilities={["*"]} />);
    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("opens create modal on button click", () => {
    mockHooks({ corpora: [makeCorpus()] });

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByText("Добавить"));

    expect(screen.getByText("Новый корпус")).toBeInTheDocument();
    expect(screen.getByText("Создать")).toBeInTheDocument();
  });

  it("shows data_class descriptions in create form", () => {
    mockHooks({ corpora: [makeCorpus()] });

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByText("Добавить"));

    expect(screen.getByText("К0 — публичные материалы")).toBeInTheDocument();
    expect(screen.getByText("К2 — персональные данные")).toBeInTheDocument();
    expect(screen.getByText("К3 — коммерческая тайна")).toBeInTheDocument();
  });

  it("shows 'без класса' for corpus without data_class", () => {
    mockHooks({
      corpora: [makeCorpus({ data_class: null })],
    });

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByText("без класса")).toBeInTheDocument();
  });

  it("shows index status", () => {
    mockHooks({
      corpora: [
        makeCorpus({ active_index_version_id: "iv-1" }),
        makeCorpus({ id: "c2", active_index_version_id: null }),
      ],
    });

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByText(/Индекс активен/)).toBeInTheDocument();
    expect(screen.getByText(/Индекс не построен/)).toBeInTheDocument();
  });

  // --- T-401: Edit data_class modal ---

  it("shows edit button when canManage", () => {
    mockHooks({ corpora: [makeCorpus()] });

    render(<CorporaPage capabilities={["*"]} />);

    expect(screen.getByTitle("Изменить класс данных")).toBeInTheDocument();
  });

  it("hides edit button when cannot manage", () => {
    mockHooks({ corpora: [makeCorpus()] });

    render(<CorporaPage capabilities={["chat"]} />);

    expect(screen.queryByTitle("Изменить класс данных")).not.toBeInTheDocument();
  });

  it("opens edit modal with current data_class", () => {
    mockHooks({ corpora: [makeCorpus({ data_class: "К2" })] });

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));

    expect(screen.getByText(/Класс данных: public/)).toBeInTheDocument();
    expect(screen.getByText("Сохранить")).toBeInTheDocument();
  });

  it("shows confirm warning on downgrade К2→К0", () => {
    mockHooks({ corpora: [makeCorpus({ data_class: "К2" })] });

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));
    const select = screen.getByDisplayValue("К2 — персональные данные");
    fireEvent.change(select, { target: { value: "К0" } });
    fireEvent.click(screen.getByText("Сохранить"));

    expect(screen.getByText("Понижение класса конфиденциальности")).toBeInTheDocument();
    expect(screen.getByText("Подтвердить понижение")).toBeInTheDocument();
  });

  it("does not show confirm on upgrade К0→К3", () => {
    mockHooks({ corpora: [makeCorpus({ data_class: "К0" })] });

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));
    const select = screen.getByDisplayValue("К0 — публичные материалы");
    fireEvent.change(select, { target: { value: "К3" } });
    fireEvent.click(screen.getByText("Сохранить"));

    expect(screen.queryByText("Понижение класса конфиденциальности")).not.toBeInTheDocument();
  });

  it("calls mutateAsync on upgrade without confirm", async () => {
    mockHooks({ corpora: [makeCorpus({ data_class: "К0", id: "c1" })] });
    mockMutateAsync.mockResolvedValue({});

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));
    const select = screen.getByDisplayValue("К0 — публичные материалы");
    fireEvent.change(select, { target: { value: "К3" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        id: "c1",
        body: { data_class: "К3" },
      });
    });
  });

  it("requires second click to confirm downgrade", async () => {
    mockHooks({ corpora: [makeCorpus({ data_class: "К3", id: "c1" })] });
    mockMutateAsync.mockResolvedValue({});

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));
    const select = screen.getByDisplayValue("К3 — коммерческая тайна");
    fireEvent.change(select, { target: { value: "К0" } });

    // First click → shows confirm, does NOT call API
    fireEvent.click(screen.getByText("Сохранить"));
    expect(mockMutateAsync).not.toHaveBeenCalled();

    // Second click → confirms, calls API
    fireEvent.click(screen.getByText("Подтвердить понижение"));
    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        id: "c1",
        body: { data_class: "К0" },
      });
    });
  });

  it("shows toast error on API failure", async () => {
    const { toast } = await import("sonner");
    mockHooks({ corpora: [makeCorpus({ data_class: "К0", id: "c1" })] });
    mockMutateAsync.mockRejectedValue(new Error("fetch failed"));

    render(<CorporaPage capabilities={["*"]} />);

    fireEvent.click(screen.getByTitle("Изменить класс данных"));
    const select = screen.getByDisplayValue("К0 — публичные материалы");
    fireEvent.change(select, { target: { value: "К3" } });
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Не удалось изменить класс данных");
    });
  });
});
