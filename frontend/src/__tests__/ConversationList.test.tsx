/**
 * T-436: ConversationList — полнотекстовый поиск по диалогам.
 *
 * Тесты:
 * - показывает строку поиска
 * - пустой инпут → обычный список диалогов
 * - ввод ≥2 символов → режим поиска (вызывает apiSearchConversations)
 * - ввод 1 символа → поиск не запускается (минимальная длина 2)
 * - пустая выдача → «Ничего не найдено»
 * - клик по результату → onSelect с conversation_id
 * - кнопка очистки → сброс поиска
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConversationList } from "../components/ConversationList";
import type { ConversationResponse } from "../api/types";
import * as conversationsApi from "../api/conversations";

vi.mock("../api/conversations", () => ({
  apiSearchConversations: vi.fn(),
}));

const mockConversations: ConversationResponse[] = [
  { id: "c1", title: "Первый диалог", archived: false, mode: "chat", created_at: "2026-01-01T00:00:00Z", message_count: 0 },
  { id: "c2", title: "Второй диалог", archived: false, mode: "chat", created_at: "2026-01-02T00:00:00Z", message_count: 0 },
];

function renderList(
  overrides: {
    conversations?: ConversationResponse[];
    onSelect?: (id: string) => void;
    onDelete?: (id: string) => void;
  } = {},
) {
  const onSelect = overrides.onSelect ?? vi.fn();
  const onDelete = overrides.onDelete;
  const conversations = overrides.conversations ?? mockConversations;
  return {
    onSelect,
    ...render(
      <ConversationList
        conversations={conversations}
        activeId={null}
        onSelect={onSelect}
        onDelete={onDelete}
      />,
    ),
  };
}

// Wait for debounce (300ms) + promise resolution
async function waitForDebounce() {
  await new Promise((r) => setTimeout(r, 450));
}

describe("ConversationList search (T-436)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows search input", () => {
    renderList();
    expect(screen.getByPlaceholderText("Поиск по диалогам…")).toBeInTheDocument();
  });

  it("shows conversation list when search is empty", () => {
    renderList();
    expect(screen.getByText("Первый диалог")).toBeInTheDocument();
    expect(screen.getByText("Второй диалог")).toBeInTheDocument();
  });

  it("does not search for 1-char query", async () => {
    renderList();
    const input = screen.getByPlaceholderText("Поиск по диалогам…");
    fireEvent.change(input, { target: { value: "a" } });
    await waitForDebounce();
    expect(conversationsApi.apiSearchConversations).not.toHaveBeenCalled();
    expect(screen.getByText("Первый диалог")).toBeInTheDocument();
  });

  it("switches to search mode for 2+ chars", async () => {
    vi.mocked(conversationsApi.apiSearchConversations).mockResolvedValue([
      { message_id: "m1", conversation_id: "c2", role: "user", content: "Hello world test", score: -1.5 },
    ]);
    renderList();
    const input = screen.getByPlaceholderText("Поиск по диалогам…");
    fireEvent.change(input, { target: { value: "hello" } });
    await waitForDebounce();
    expect(conversationsApi.apiSearchConversations).toHaveBeenCalledWith("hello");
    await waitFor(() => {
      expect(screen.getByText("Hello world test")).toBeInTheDocument();
    });
    expect(screen.queryByText("Первый диалог")).not.toBeInTheDocument();
  });

  it("shows empty state when no results", async () => {
    vi.mocked(conversationsApi.apiSearchConversations).mockResolvedValue([]);
    renderList();
    const input = screen.getByPlaceholderText("Поиск по диалогам…");
    fireEvent.change(input, { target: { value: "nomatch" } });
    await waitForDebounce();
    await waitFor(() => {
      expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
    });
  });

  it("calls onSelect with conversation_id when result clicked", async () => {
    vi.mocked(conversationsApi.apiSearchConversations).mockResolvedValue([
      { message_id: "m1", conversation_id: "c2", role: "user", content: "Hello world", score: -1.5 },
    ]);
    const onSelect = vi.fn();
    renderList({ onSelect });
    const input = screen.getByPlaceholderText("Поиск по диалогам…");
    fireEvent.change(input, { target: { value: "hello" } });
    await waitForDebounce();
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Hello world"));
    expect(onSelect).toHaveBeenCalledWith("c2");
  });

  it("clears search when X button clicked", async () => {
    vi.mocked(conversationsApi.apiSearchConversations).mockResolvedValue([
      { message_id: "m1", conversation_id: "c2", role: "user", content: "Hello", score: -1.5 },
    ]);
    renderList();
    const input = screen.getByPlaceholderText("Поиск по диалогам…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hello" } });
    await waitForDebounce();
    await waitFor(() => {
      expect(screen.getByText("Hello")).toBeInTheDocument();
    });
    const clearBtn = screen.getByLabelText("Очистить поиск");
    fireEvent.click(clearBtn);
    expect(input.value).toBe("");
    await waitFor(() => {
      expect(screen.getByText("Первый диалог")).toBeInTheDocument();
    });
  });
});

describe("ConversationList delete (T-443)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("hides delete button when onDelete not provided", () => {
    renderList();
    expect(screen.queryByLabelText("Удалить диалог")).not.toBeInTheDocument();
  });

  it("shows delete buttons when onDelete provided", () => {
    renderList({ onDelete: vi.fn() });
    expect(screen.getAllByLabelText("Удалить диалог")).toHaveLength(2);
  });

  it("shows inline confirm and calls onDelete on confirm", () => {
    const onDelete = vi.fn();
    renderList({ onDelete });
    const deleteButtons = screen.getAllByLabelText("Удалить диалог");
    fireEvent.click(deleteButtons[0]);
    // Инлайн-подтверждение с именем диалога
    expect(screen.getByTestId("delete-confirm")).toBeInTheDocument();
    expect(screen.getByText(/Удалить «Первый диалог»/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Удалить"));
    expect(onDelete).toHaveBeenCalledWith("c1");
  });

  it("cancel hides confirm without calling onDelete", () => {
    const onDelete = vi.fn();
    renderList({ onDelete });
    const deleteButtons = screen.getAllByLabelText("Удалить диалог");
    fireEvent.click(deleteButtons[1]);
    expect(screen.getByTestId("delete-confirm")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Отмена"));
    expect(screen.queryByTestId("delete-confirm")).not.toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();
  });
});
