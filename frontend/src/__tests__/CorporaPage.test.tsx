import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CorporaPage } from "../pages/CorporaPage";
import { useCorpora, useCreateCorpus } from "../hooks/useCorpora";
import type { CorpusListResponse } from "../api/types";

vi.mock("../hooks/useCorpora");

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
});
