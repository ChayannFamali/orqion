import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CorpusSelector } from "../components/CorpusSelector";
import type { AvailableCorpusEntry } from "../api/types";

function entry(overrides: Partial<AvailableCorpusEntry> = {}): AvailableCorpusEntry {
  return {
    id: "c1",
    name: "corpus-a",
    data_class: null,
    ready: true,
    ...overrides,
  };
}

describe("CorpusSelector (T-439)", () => {
  it("ничего не рисует без корпусов", () => {
    const { container } = render(
      <CorpusSelector corpora={[]} selected={[]} onToggle={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("показывает все корпуса с классом данных", () => {
    render(
      <CorpusSelector
        corpora={[entry(), entry({ id: "c2", name: "corpus-b", data_class: "К2" })]}
        selected={[]}
        onToggle={vi.fn()}
      />,
    );
    expect(screen.getByText("corpus-a")).toBeInTheDocument();
    expect(screen.getByText("corpus-b · К2")).toBeInTheDocument();
  });

  it("клик переключает выбор", () => {
    const onToggle = vi.fn();
    render(<CorpusSelector corpora={[entry()]} selected={[]} onToggle={onToggle} />);
    fireEvent.click(screen.getByText("corpus-a"));
    expect(onToggle).toHaveBeenCalledWith("corpus-a");
  });

  it("выбранный корпус помечен активным стилем", () => {
    render(<CorpusSelector corpora={[entry()]} selected={["corpus-a"]} onToggle={vi.fn()} />);
    const chip = screen.getByText("corpus-a");
    expect(chip.className).toContain("bg-primary");
  });

  it("неготовый корпус заблокирован с подсказкой", () => {
    const onToggle = vi.fn();
    render(
      <CorpusSelector
        corpora={[entry({ ready: false })]}
        selected={[]}
        onToggle={onToggle}
      />,
    );
    const chip = screen.getByText("corpus-a");
    expect(chip.closest("button")).toBeDisabled();
    fireEvent.click(chip);
    expect(onToggle).not.toHaveBeenCalled();
  });
});
