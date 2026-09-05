/**
 * Т-508 (попутный фикс): подписи видов шагов в ленте прогона.
 *
 * До правки подпись выбиралась бинарным тернарником «инструмент / иначе
 * модель», поэтому шаги подтверждения (Т-503) подписывались «Модель» и в
 * ленте читалось «2. Модель Ожидает подтверждения пользователя». Теперь
 * виды сопоставляются явно, а неизвестный вид показывается как есть —
 * новый шаг не может быть молча подписан чужим именем.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentRunSummary } from "../components/AgentRunSummary";
import type { AgentStepEntry } from "../api/types";

function step(overrides: Partial<AgentStepEntry> = {}): AgentStepEntry {
  return {
    index: 1,
    kind: "model",
    name: null,
    summary: "Запрошены инструменты",
    decision: null,
    ...overrides,
  };
}

describe("AgentRunSummary: подписи видов шагов", () => {
  it("не рендерится без шагов", () => {
    const { container } = render(<AgentRunSummary steps={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("модельный шаг подписан «Модель»", () => {
    render(<AgentRunSummary steps={[step({ kind: "model", summary: "Финальный ответ" })]} />);
    expect(screen.getByText("Модель")).toBeInTheDocument();
    expect(screen.getByText(/Финальный ответ/)).toBeInTheDocument();
  });

  it("шаг инструмента подписан «Инструмент» с именем", () => {
    render(
      <AgentRunSummary
        steps={[
          step({
            kind: "tool",
            name: "search_corpus",
            summary: "Фрагментов: 3",
            decision: "allow",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Инструмент search_corpus")).toBeInTheDocument();
    // Решение allow дополнительной подписи не получает.
    expect(screen.getByText("Фрагментов: 3")).toBeInTheDocument();
  });

  it("дефект Т-503: шаг подтверждения больше не подписан «Модель»", () => {
    render(
      <AgentRunSummary
        steps={[
          step({
            index: 2,
            kind: "confirmation",
            name: "demo-build.drop_cache",
            summary: "Ожидает подтверждения пользователя",
            decision: "pending",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Подтверждение demo-build.drop_cache")).toBeInTheDocument();
    expect(screen.queryByText(/Модель/)).not.toBeInTheDocument();
    // Решение не дублирует summary: текст уже говорит об ожидании.
    expect(screen.getByText("Ожидает подтверждения пользователя")).toBeInTheDocument();
  });

  it("шаги одобрения и отмены подписаны «Подтверждение» без дубля решения", () => {
    const { unmount } = render(
      <AgentRunSummary
        steps={[
          step({
            kind: "confirmation",
            name: "demo.drop",
            summary: "Выполнено после подтверждения",
            decision: "approve",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Подтверждение demo.drop")).toBeInTheDocument();
    unmount();

    render(
      <AgentRunSummary
        steps={[
          step({
            kind: "confirmation",
            name: "demo.drop",
            summary: "Отменено пользователем",
            decision: "reject",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Отменено пользователем")).toBeInTheDocument();
  });

  it("Т-508: шаг скилла подписан «Скилл» с именем и числом инструментов", () => {
    render(
      <AgentRunSummary
        steps={[
          step({
            index: 1,
            kind: "skill",
            name: "Разбор",
            summary: "Инструментов в прогоне: 2",
          }),
          step({ index: 2, kind: "model", summary: "Финальный ответ" }),
        ]}
      />,
    );
    expect(screen.getByText("Скилл Разбор")).toBeInTheDocument();
    expect(screen.getByText(/Инструментов в прогоне: 2/)).toBeInTheDocument();
    expect(screen.getByText("Модель")).toBeInTheDocument();
  });

  it("шаг скилла без инструментов честно показывает ноль", () => {
    render(
      <AgentRunSummary
        steps={[
          step({
            kind: "skill",
            name: "Только инструкции",
            summary: "Инструментов в прогоне: 0",
          }),
        ]}
      />,
    );
    expect(screen.getByText("Скилл Только инструкции")).toBeInTheDocument();
    expect(screen.getByText(/Инструментов в прогоне: 0/)).toBeInTheDocument();
  });

  it("отказ политики подписан пояснением", () => {
    render(
      <AgentRunSummary
        steps={[
          step({
            kind: "tool",
            name: "search_corpus",
            summary: "Недоступно для роли",
            decision: "deny",
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Недоступно для роли \(отказ политики\)/)).toBeInTheDocument();
  });

  it("неизвестный вид показывается как есть, а не «Модель»", () => {
    render(
      <AgentRunSummary
        steps={[step({ kind: "planning", name: null, summary: "Новый вид шага" })]}
      />,
    );
    expect(screen.getByText("planning")).toBeInTheDocument();
    expect(screen.queryByText("Модель")).not.toBeInTheDocument();
  });
});
