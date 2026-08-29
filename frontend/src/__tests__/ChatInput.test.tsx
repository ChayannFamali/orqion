import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatInput } from "../components/ChatInput";

/**
 * Т-507: быстрый выбор сохранённых промптов у поля ввода чата.
 *
 * Приёмка: пикер виден только при наличии шаблонов; клик по шаблону
 * вставляет текст в поле ввода (пользователь правит и отправляет);
 * непустое поле — дописывается с новой строки.
 */

const TEMPLATES = [
  { id: "pt1", title: "Код-ревью", body: "Проведи код-ревью файла" },
  { id: "pt2", title: "Саммари", body: "Суммируй документ" },
];

function renderInput(templates?: typeof TEMPLATES) {
  return render(
    <ChatInput
      onSend={vi.fn()}
      onAbort={vi.fn()}
      isStreaming={false}
      templates={templates}
    />,
  );
}

describe("ChatInput — выбор шаблонов промптов (Т-507)", () => {
  it("пикер не показывается без шаблонов", () => {
    renderInput([]);
    expect(screen.queryByTestId("prompt-template-picker")).not.toBeInTheDocument();
  });

  it("пикер показывается при наличии шаблонов", () => {
    renderInput(TEMPLATES);
    expect(screen.getByTestId("prompt-template-picker")).toBeInTheDocument();
  });

  it("клик по шаблону вставляет его текст в поле ввода", () => {
    renderInput(TEMPLATES);

    fireEvent.click(screen.getByTestId("prompt-template-picker"));
    const options = screen.getAllByTestId("prompt-template-option");
    expect(options).toHaveLength(2);

    fireEvent.click(options[0]);

    const textarea = screen.getByTestId("chat-input-textarea");
    expect(textarea).toHaveValue("Проведи код-ревью файла");
  });

  it("меню закрывается после выбора", () => {
    renderInput(TEMPLATES);

    fireEvent.click(screen.getByTestId("prompt-template-picker"));
    fireEvent.click(screen.getAllByTestId("prompt-template-option")[1]);

    expect(screen.queryByTestId("prompt-template-menu")).not.toBeInTheDocument();
  });

  it("к непустому полю текст дописывается с новой строки", () => {
    renderInput(TEMPLATES);

    const textarea = screen.getByTestId("chat-input-textarea");
    fireEvent.change(textarea, { target: { value: "Пожалуйста:" } });

    fireEvent.click(screen.getByTestId("prompt-template-picker"));
    fireEvent.click(screen.getAllByTestId("prompt-template-option")[0]);

    expect(textarea).toHaveValue("Пожалуйста:\nПроведи код-ревью файла");
  });

  it("после вставки текст можно отправить обычным порядком", () => {
    const onSend = vi.fn();
    render(
      <ChatInput onSend={onSend} onAbort={vi.fn()} isStreaming={false} templates={TEMPLATES} />,
    );

    fireEvent.click(screen.getByTestId("prompt-template-picker"));
    fireEvent.click(screen.getAllByTestId("prompt-template-option")[1]);
    fireEvent.click(screen.getByText("Отправить"));

    expect(onSend).toHaveBeenCalledWith("Суммируй документ");
  });
});
