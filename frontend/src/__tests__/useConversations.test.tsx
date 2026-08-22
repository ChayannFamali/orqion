/**
 * T-433: refetchInterval в useConversations.
 *
 * refetchInterval=5000 когда есть диалоги с пустым title (заголовок ещё
 * генерируется фоновой задачей). refetchInterval=false когда все title
 * заполнены — не вечный поллинг.
 *
 * Тестируем refetchInterval callback напрямую: TanStack Query v5
 * передаёт query object в callback, мы проверяем возвращаемое значение.
 */
import { describe, it, expect } from "vitest";

// Извлекаем refetchInterval callback из useConversations путём
// перехвата useQuery opts. Косвенный, но стабильный способ —
// парсим исходник useConversations.ts и проверяем логику.

// Прямой способ: импортируем хук и мокаем useQuery, перехватывая opts.
import { vi } from "vitest";

// Мокаем @tanstack/react-query, перехватывая useQuery
let capturedOpts: { refetchInterval?: (query: unknown) => unknown } | undefined;

vi.mock("@tanstack/react-query", () => ({
  useQuery: (opts: { refetchInterval?: (query: unknown) => unknown }) => {
    capturedOpts = opts;
    return { data: undefined, isLoading: true };
  },
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

import { useConversations } from "../hooks/useConversations";

describe("useConversations refetchInterval (T-433)", () => {
  it("refetchInterval returns 5000 when conversations have empty title", () => {
    useConversations();
    expect(capturedOpts?.refetchInterval).toBeDefined();

    const fn = capturedOpts!.refetchInterval!;
    const query = {
      state: {
        data: {
          conversations: [
            { id: "c1", title: "" },
            { id: "c2", title: "Has Title" },
          ],
        },
      },
    };
    expect(fn(query)).toBe(5000);
  });

  it("refetchInterval returns false when all conversations have titles", () => {
    useConversations();
    expect(capturedOpts?.refetchInterval).toBeDefined();

    const fn = capturedOpts!.refetchInterval!;
    const query = {
      state: {
        data: {
          conversations: [
            { id: "c1", title: "Title A" },
            { id: "c2", title: "Title B" },
          ],
        },
      },
    };
    expect(fn(query)).toBe(false);
  });

  it("refetchInterval returns false when conversation list is empty", () => {
    useConversations();
    expect(capturedOpts?.refetchInterval).toBeDefined();

    const fn = capturedOpts!.refetchInterval!;
    const query = {
      state: {
        data: {
          conversations: [],
        },
      },
    };
    expect(fn(query)).toBe(false);
  });

  it("refetchInterval returns false when data is undefined", () => {
    useConversations();
    expect(capturedOpts?.refetchInterval).toBeDefined();

    const fn = capturedOpts!.refetchInterval!;
    const query = { state: { data: undefined } };
    expect(fn(query)).toBe(false);
  });
});
