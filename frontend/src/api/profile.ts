import { apiFetch } from "./client";
import type {
  UserPreferenceListResponse,
  UserPreferenceResponse,
  UserPreferenceUpdate,
} from "./types";

/**
 * Ключ личной настройки: какое сочетание клавиш отправляет сообщение.
 *
 * Объявлен здесь, а не в компоненте: значение приходит с сервера и уходит
 * на сервер, а компонент ввода получает его уже готовым пропсом и не знает
 * ни про реестр настроек, ни про запросы.
 */
export const CHAT_SEND_KEY = "chat_send_key";

/** Допустимые значения ключа `chat_send_key` (из перечисления в реестре). */
export type SendKeyMode = "enter" | "shift_enter";

/**
 * Личные настройки пользователя (раздел «Профиль»).
 *
 * В отличие от служебных настроек рабочей области доступ здесь — владение
 * строкой, а не способность роли: сервер отдаёт только настройки
 * запросившего пользователя, поэтому `editable` всегда true и отдельной
 * проверки прав на клиенте нет. Запись идёт по одному ключу за вызов — при
 * отказе понятно, какое именно поле его вызвало.
 */
export async function apiGetUserPreferences(): Promise<UserPreferenceListResponse> {
  return apiFetch<UserPreferenceListResponse>("/api/profile/preferences");
}

export async function apiUpdateUserPreference(
  body: UserPreferenceUpdate,
): Promise<UserPreferenceResponse> {
  return apiFetch<UserPreferenceResponse>("/api/profile/preferences", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
