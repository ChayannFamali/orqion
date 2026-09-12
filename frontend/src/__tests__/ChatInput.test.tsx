import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ChatInput } from "../components/ChatInput";
import type { SendKeyMode } from "../api/profile";

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

/**
 * Т-512: способ отправки сообщения — личная настройка пользователя.
 *
 * Приёмка: значение приходит пропсом; Enter отправляет по умолчанию и при
 * явном ``enter``; при ``shift_enter`` отправляет Shift+Enter, а чистый
 * Enter остаётся переносом строки (событие не перехватывается); подсказка в
 * поле соответствует выбранному режиму. Кнопка «Отправить» работает
 * одинаково в обоих режимах.
 */
function renderInputWithMode(sendMode?: SendKeyMode) {
  const onSend = vi.fn();
  render(
    <ChatInput onSend={onSend} onAbort={vi.fn()} isStreaming={false} sendMode={sendMode} />,
  );
  return { onSend, textarea: screen.getByTestId("chat-input-textarea") };
}

describe("ChatInput — способ отправки сообщения (Т-512)", () => {
  it("по умолчанию Enter отправляет, Shift+Enter — перенос строки", () => {
    const { onSend, textarea } = renderInputWithMode();

    fireEvent.change(textarea, { target: { value: "привет" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("привет");
  });

  it("явный режим enter повторяет поведение по умолчанию", () => {
    const { onSend, textarea } = renderInputWithMode("enter");

    fireEvent.change(textarea, { target: { value: "привет" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("привет");
  });

  it("режим shift_enter: Shift+Enter отправляет, чистый Enter — нет", () => {
    const { onSend, textarea } = renderInputWithMode("shift_enter");

    fireEvent.change(textarea, { target: { value: "первая\nвторая" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).toHaveBeenCalledWith("первая\nвторая");
  });

  it("чистый Enter в режиме shift_enter не перехватывается (перенос строки)", () => {
    const { textarea } = renderInputWithMode("shift_enter");

    fireEvent.change(textarea, { target: { value: "текст" } });
    // fireEvent возвращает false, если обработчик отменил событие —
    // неотменённый Enter доходит до textarea как перенос строки.
    expect(fireEvent.keyDown(textarea, { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true })).toBe(false);
  });

  it("кнопка «Отправить» работает в обоих режимах", () => {
    for (const mode of [undefined, "enter", "shift_enter"] as const) {
      cleanup();
      const { onSend, textarea } = renderInputWithMode(mode);
      fireEvent.change(textarea, { target: { value: "текст" } });
      fireEvent.click(screen.getByText("Отправить"));
      expect(onSend).toHaveBeenCalledWith("текст");
    }
  });

  it("подсказка соответствует выбранному режиму", () => {
    const { textarea: enterField } = renderInputWithMode("enter");
    expect(enterField.getAttribute("placeholder")).toContain("Enter — отправить");

    cleanup();
    const { textarea: shiftField } = renderInputWithMode("shift_enter");
    expect(shiftField.getAttribute("placeholder")).toContain("Shift+Enter — отправить");
  });

  it("другие клавиши не отправляют сообщение", () => {
    const { onSend, textarea } = renderInputWithMode("shift_enter");

    fireEvent.change(textarea, { target: { value: "текст" } });
    fireEvent.keyDown(textarea, { key: "a", shiftKey: true });
    fireEvent.keyDown(textarea, { key: "Shift" });

    expect(onSend).not.toHaveBeenCalled();
  });
});
