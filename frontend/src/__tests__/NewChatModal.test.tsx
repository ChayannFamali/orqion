import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NewChatModal } from "../components/NewChatModal";
import type { ModelInfo } from "../api/types";

function makeModel(alias: string, kind: string, locality = "local"): ModelInfo {
  return {
    id: alias,
    alias,
    upstream_name: alias,
    locality,
    provider_kind: kind,
    max_input_tokens: null,
    max_output_tokens: null,
    supports_reasoning: false,
    reasoning_toggleable: false,
    cost_in: null,
    cost_out: null,
    enabled: true,
  };
}

const models = [
  makeModel("glm-5.2-free", "open_router", "external"),
  makeModel("prism-ml/bonsai-27b", "lm"),
];

describe("NewChatModal", () => {
  it("renders flat list with provider and locality labels", () => {
    render(
      <NewChatModal open models={models} onCancel={vi.fn()} onCreate={vi.fn()} />,
    );
    expect(screen.getByText("glm-5.2-free")).toBeInTheDocument();
    expect(screen.getByText(/open_router · external/)).toBeInTheDocument();
    expect(screen.getByText(/lm · local/)).toBeInTheDocument();
  });

  it("create is disabled until a model is selected", async () => {
    const onCreate = vi.fn();
    render(
      <NewChatModal open models={models} onCancel={vi.fn()} onCreate={onCreate} />,
    );
    const create = screen.getByText("Создать диалог");
    expect(create).toBeDisabled();
    const user = userEvent.setup();
    await user.click(screen.getByText("glm-5.2-free"));
    expect(create).toBeEnabled();
    await user.click(create);
    expect(onCreate).toHaveBeenCalledWith("glm-5.2-free");
  });

  it("escape and cancel call onCancel", async () => {
    const onCancel = vi.fn();
    render(
      <NewChatModal open models={models} onCancel={onCancel} onCreate={vi.fn()} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByText("Отмена"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("groups by provider only beyond threshold", () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      makeModel(`m-${String(i).padStart(2, "0")}`, i % 2 === 0 ? "aa" : "bb"),
    );
    const { unmount } = render(
      <NewChatModal open models={many} onCancel={vi.fn()} onCreate={vi.fn()} />,
    );
    expect(screen.getByText("aa")).toBeInTheDocument();
    expect(screen.getByText("bb")).toBeInTheDocument();
    unmount();

    render(
      <NewChatModal open models={models} onCancel={vi.fn()} onCreate={vi.fn()} />,
    );
    // короткий список — плоский, без заголовков групп
    expect(screen.queryByText("open_router")).not.toBeInTheDocument();
    expect(screen.queryByText("lm")).not.toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    render(
      <NewChatModal open={false} models={models} onCancel={vi.fn()} onCreate={vi.fn()} />,
    );
    expect(screen.queryByText("Создать диалог")).not.toBeInTheDocument();
  });
});
