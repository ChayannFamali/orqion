import { useEffect, useState } from "react";
import type { WorkspaceSettingResponse } from "../api/types";

/** Значение настройки в терминах запроса: скаляр, который принимает JSON. */
export type SettingValue = string | number | boolean;

interface SettingFieldProps {
  setting: WorkspaceSettingResponse;
  /** Текст ошибки сохранения именно этого ключа; null — ошибки нет. */
  error: string | null;
  /** Идёт ли запись этого ключа прямо сейчас. */
  pending: boolean;
  onCommit: (value: SettingValue) => void;
}

const inputClass =
  "w-48 rounded-md border border-border bg-background px-3 py-2 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-60";

/**
 * Универсальное поле служебной настройки.
 *
 * Вид поля целиком определяется ответом API: тип, опции перечисления,
 * границы и право на запись приходят в описании ключа, поэтому новая
 * настройка не требует ни строчки кода здесь. Никакой ветки по имени ключа
 * нет намеренно — иначе каждое добавление ключа снова означало бы правку
 * фронтенда.
 *
 * Значение уходит на сервер отдельным запросом при потере фокуса (текст и
 * число) или сразу при переключении (флаг и перечисление): общей кнопки
 * «Сохранить всё» нет, чтобы отказ всегда был привязан к конкретному полю.
 */
export function SettingField({ setting, error, pending, onCommit }: SettingFieldProps) {
  const serverValue = setting.value;
  const [draft, setDraft] = useState(() => toDraft(serverValue));
  const [localError, setLocalError] = useState<string | null>(null);

  // Сервер — источник правды: после сохранения или чужой записи черновик
  // сходится с пришедшим значением. Зависимость от самого значения, а не от
  // объекта настройки, чтобы сохранение соседнего ключа не сбрасывало ввод.
  useEffect(() => {
    setDraft(toDraft(serverValue));
    setLocalError(null);
  }, [serverValue]);

  // Отказ сохранения: черновик возвращается к значению сервера, иначе поле
  // показывало бы то, чего на сервере нет.
  useEffect(() => {
    if (error !== null) setDraft(toDraft(serverValue));
  }, [error, serverValue]);

  const commitNumber = () => {
    const text = draft.trim();
    if (text === "") {
      setLocalError("Введите значение");
      return;
    }
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) {
      setLocalError("Введите число");
      return;
    }
    if (setting.type === "integer" && !Number.isInteger(parsed)) {
      setLocalError("Введите целое число");
      return;
    }
    setLocalError(null);
    if (parsed !== serverValue) onCommit(parsed);
  };

  const commitText = () => {
    setLocalError(null);
    if (draft !== serverValue) onCommit(draft);
  };

  const shownError = localError ?? error;

  return (
    <div className="space-y-1" data-testid={`setting-block-${setting.key}`}>
      <div className="flex items-center gap-2">
        <label htmlFor={`setting-${setting.key}`} className="text-sm font-medium">
          {setting.title}
        </label>
        <span className="text-xs text-muted-foreground" data-testid={`setting-source-${setting.key}`}>
          {setting.source === "db" ? "изменено" : "по умолчанию"}
        </span>
        {pending && <span className="text-xs text-muted-foreground">Сохранение…</span>}
      </div>

      <p className="text-xs text-muted-foreground">{setting.description}</p>

      {setting.type === "boolean" ? (
        <label className="flex items-center gap-2 pt-1 text-sm">
          <input
            id={`setting-${setting.key}`}
            type="checkbox"
            checked={draft === "true"}
            disabled={!setting.editable || pending}
            onChange={(e) => {
              setDraft(e.target.checked ? "true" : "false");
              onCommit(e.target.checked);
            }}
            data-testid={`setting-field-${setting.key}`}
          />
          <span className="text-muted-foreground">{draft === "true" ? "включено" : "выключено"}</span>
        </label>
      ) : setting.type === "enum" ? (
        <select
          id={`setting-${setting.key}`}
          value={draft}
          disabled={!setting.editable || pending}
          onChange={(e) => {
            setDraft(e.target.value);
            onCommit(e.target.value);
          }}
          className={inputClass}
          data-testid={`setting-field-${setting.key}`}
        >
          {(setting.enum_values ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : setting.type === "integer" || setting.type === "number" ? (
        <input
          id={`setting-${setting.key}`}
          type="number"
          value={draft}
          min={setting.min ?? undefined}
          max={setting.max ?? undefined}
          step={setting.type === "integer" ? 1 : undefined}
          disabled={!setting.editable || pending}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitNumber}
          className={inputClass}
          data-testid={`setting-field-${setting.key}`}
        />
      ) : (
        <input
          id={`setting-${setting.key}`}
          type="text"
          value={draft}
          disabled={!setting.editable || pending}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitText}
          className={`${inputClass} w-full`}
          data-testid={`setting-field-${setting.key}`}
        />
      )}

      {!setting.editable && (
        <p className="text-xs text-muted-foreground" data-testid={`setting-readonly-${setting.key}`}>
          Изменение доступно с правом управления настройками.
        </p>
      )}

      {shownError && (
        <p className="text-xs text-destructive" data-testid={`setting-error-${setting.key}`}>
          {shownError}
        </p>
      )}
    </div>
  );
}

/** Черновик ввода — всегда строка: поле ввода не различает 5 и «5». */
function toDraft(value: unknown): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "";
  return String(value);
}
