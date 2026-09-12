import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "../api/query-keys";
import {
  CHAT_SEND_KEY,
  apiGetUserPreferences,
  apiUpdateUserPreference,
  type SendKeyMode,
} from "../api/profile";
import type { UserPreferenceUpdate } from "../api/types";

/**
 * Личные настройки пользователя (раздел «Профиль»).
 *
 * Запись одного ключа инвалидирует весь список: источник значения
 * (`default` → `db`) приходит одним ответом, а частичное обновление кэша
 * разошлось бы с сервером.
 *
 * `staleTime` большой: состав ключей и их значения меняются только
 * действием самого пользователя, а читает их каждый заход в чат —
 * запрашивать список при каждом переключении раздела смысла нет.
 */
export function useUserPreferences() {
  return useQuery({
    queryKey: queryKeys.profilePreferences.all,
    queryFn: apiGetUserPreferences,
    staleTime: 300_000,
  });
}

export function useUpdateUserPreference() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UserPreferenceUpdate) => apiUpdateUserPreference(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.profilePreferences.all });
    },
  });
}

/**
 * Какое сочетание клавиш отправляет сообщение в чате.
 *
 * До ответа сервера и при ошибке загрузки — `enter`: это поведение поля
 * ввода по умолчанию, поэтому ожидание ответа не блокирует чат и не
 * подменяет привычное сочетание. Неизвестное значение (например, ключ
 * переименован на сервере) трактуется так же.
 */
export function useSendKeyMode(): SendKeyMode {
  const { data } = useUserPreferences();
  const value = data?.preferences.find((item) => item.key === CHAT_SEND_KEY)?.value;
  return value === "shift_enter" ? "shift_enter" : "enter";
}
