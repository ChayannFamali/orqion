import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelSelector } from "../components/ModelSelector";
import type { ModelInfo } from "../api/types";

function makeModel(overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id: "m1",
    alias: "qwen-7b",
    upstream_name: "qwen2.5-7b",
    locality: "local",
    max_input_tokens: 32768,
    max_output_tokens: 4096,
    supports_reasoning: false,
    cost_in: null,
    cost_out: null,
    enabled: true,
    ...overrides,
  };
}

describe("ModelSelector", () => {
  it("renders available models", () => {
    const models = [
      makeModel({ id: "m1", alias: "qwen-7b", locality: "local" }),
      makeModel({ id: "m2", alias: "gpt-4", locality: "external" }),
    ];
    render(
      <ModelSelector models={models} value="qwen-7b" onChange={vi.fn()} />,
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.children.length).toBe(2);
    expect(select.options[0].textContent).toContain("qwen-7b");
    expect(select.options[0].textContent).toContain("local");
    expect(select.options[1].textContent).toContain("gpt-4");
    expect(select.options[1].textContent).toContain("external");
  });

  it("calls onChange when selection changes", () => {
    const onChange = vi.fn();
    const models = [
      makeModel({ id: "m1", alias: "qwen-7b" }),
      makeModel({ id: "m2", alias: "llama-8b" }),
    ];
    render(<ModelSelector models={models} value="qwen-7b" onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "llama-8b" } });
    expect(onChange).toHaveBeenCalledWith("llama-8b");
  });

  it("shows no-models message when list is empty", () => {
    render(<ModelSelector models={[]} value={null} onChange={vi.fn()} />);
    expect(screen.getByText("Нет доступных моделей")).toBeInTheDocument();
  });

  it("is disabled when disabled prop is true", () => {
    const models = [makeModel()];
    render(
      <ModelSelector models={models} value="qwen-7b" onChange={vi.fn()} disabled />,
    );
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
});
