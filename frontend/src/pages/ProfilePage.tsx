import { useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { SettingField, type SettingValue } from "../components/SettingField";
import { useTheme } from "../hooks/useTheme";
import {
  useUpdateUserPreference,
  useUserPreferences,
} from "../hooks/useUserPreferences";
import type { ApiError } from "../api/runtime";
import type { UserPreferenceResponse } from "../api/types";

const selectClass =
  "w-64 rounded-md border border-border bg-background px-3 py-2 text-sm " +
  "focus:outline-none focus:ring-2 focus:ring-primary";

/**
 * Профиль — личные настройки пользователя.
 *
 * Отдельный раздел, не вкладка «Настроек»: там служебные параметры рабочей
 * области и право на запись выдаётся ролью, здесь всё принадлежит одному
 * человеку и меняется им без всякого права.
 *
 * Состав полей задаёт ответ API (категории становятся заголовками блоков),
 * поэтому новая личная настройка появляется здесь без правки фронтенда.
 * Исключение — тема оформления: она живёт в браузере и на сервер не
 * отправляется, поэтому рисуется отдельным постоянным блоком.
 */
export function ProfilePage() {
  const { theme, setTheme } = useTheme();
  const { data, isLoading, isError } = useUserPreferences();
  const updateMutation = useUpdateUserPreference();
  const [errorByKey, setErrorByKey] = useState<Record<string, string | null>>({});

  // Категории приходят вместе со списком: порядок задаёт сервер, поэтому
  // новый ключ добавляет блок сам, без перечисления категорий в коде.
  const categories = useMemo(
    () => (data ? [...new Set(data.preferences.map((item) => item.category))] : []),
    [data],
  );

  const handleCommit = (key: string, value: SettingValue) => {
    setErrorByKey((prev) => ({ ...prev, [key]: null }));
    updateMutation.mutate(
      { key, value },
      {
        onSuccess: () => toast.success("Настройка сохранена"),
        onError: (err) => {
          // Ошибка API приходит плоским объектом, а не экземпляром Error.
          const apiError = err as unknown as ApiError;
          const message = apiError.hint ?? apiError.reason ?? "Не удалось сохранить настройку";
          setErrorByKey((prev) => ({ ...prev, [key]: message }));
        },
      },
    );
  };

  const pendingKey = updateMutation.isPending ? updateMutation.variables?.key : undefined;

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-3xl space-y-4">
        <div className="space-y-1">
          <h2 className="text-xl font-bold">Профиль</h2>
          <p className="text-sm text-muted-foreground">
            Личные настройки: видны и действуют только для вашей учётной записи.
          </p>
        </div>

        <section
          className="space-y-3 rounded-lg border border-border bg-card p-4"
          data-testid="profile-appearance"
        >
          <h3 className="text-sm font-semibold text-muted-foreground">Оформление</h3>
          <div className="space-y-1">
            <label htmlFor="profile-theme" className="text-sm font-medium">
              Тема оформления
            </label>
            <p className="text-xs text-muted-foreground">
              Сохраняется в этом браузере: на другом устройстве тему нужно выбрать заново.
            </p>
            <select
              id="profile-theme"
              // Неизвестное значение из localStorage трактуется как светлая
              // тема: иначе поле осталось бы пустым и не показывало бы
              // текущее оформление.
              value={theme === "dark" ? "dark" : "light"}
              onChange={(e) => setTheme(e.target.value === "dark" ? "dark" : "light")}
              className={selectClass}
              data-testid="theme-field"
            >
              <option value="light">Светлая</option>
              <option value="dark">Тёмная</option>
            </select>
          </div>
        </section>

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span>Загрузка настроек…</span>
          </div>
        ) : isError || !data ? (
          <div
            className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground"
            data-testid="profile-preferences-error"
          >
            Не удалось загрузить личные настройки.
          </div>
        ) : data.preferences.length === 0 ? (
          <div
            className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground"
            data-testid="profile-preferences-empty"
          >
            Личных настроек пока нет.
          </div>
        ) : (
          categories.map((category) => (
            <section
              key={category}
              className="space-y-6 rounded-lg border border-border bg-card p-4"
              data-testid={`profile-category-${category}`}
            >
              <h3 className="text-sm font-semibold text-muted-foreground">{category}</h3>
              {data.preferences
                .filter((item) => item.category === category)
                .map((preference: UserPreferenceResponse) => (
                  <SettingField
                    key={preference.key}
                    setting={preference}
                    error={errorByKey[preference.key] ?? null}
                    pending={pendingKey === preference.key}
                    onCommit={(value) => handleCommit(preference.key, value)}
                  />
                ))}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
