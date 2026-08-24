/**
 * T-441: прогноз расхода бюджета (решения дизайн-ревью А1/В3/Г1).
 *
 * Метод (А1): линейная экстраполяция текущего месяца —
 * темп = использовано_за_месяц / прошедшие_дни; день исчерпания =
 * лимит / темп. Скользящее среднее по истории не используется: на новом
 * развёртывании её нет первые 1–2 месяца.
 *
 * Деградация (зафиксирована): < 3 дней данных → «недостаточно данных»,
 * прогноз не показывается вовсе (не неверный прогноз, а его отсутствие);
 * темп = 0 или день исчерпания за пределами месяца → «не исчерпается до
 * конца месяца».
 *
 * Измерения (В3): токены и стоимость считаются независимо. Лимит null
 * или <= 0 → прогноза по измерению нет; в частности cost_month = 0 у
 * пресетов (user/analyst) — это НЕ «лимит исчерпан», а отсутствие
 * прогноза (явное требование дизайн-ревью).
 */

export const MIN_FORECAST_DAYS = 3;

export type BudgetForecast =
  | { kind: "insufficient_data" }
  | { kind: "no_exhaustion" }
  | { kind: "exhaustion"; day: number };

export interface MonthProgress {
  daysElapsed: number;
  daysInMonth: number;
}

/** Прогресс текущего месяца: прошедшие дни (включая сегодня) и длина месяца. */
export function monthProgress(now: Date): MonthProgress {
  return {
    daysElapsed: now.getDate(),
    daysInMonth: new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate(),
  };
}

/**
 * Прогноз по одному измерению. Возвращает null, когда прогноза по
 * измерению нет (лимит не задан или вырожден <= 0).
 */
export function forecastDimension(
  used: number,
  limit: number | null | undefined,
  daysElapsed: number,
  daysInMonth: number,
): BudgetForecast | null {
  if (limit == null || limit <= 0) return null;
  if (daysElapsed < MIN_FORECAST_DAYS) return { kind: "insufficient_data" };
  const rate = used / daysElapsed;
  if (rate <= 0) return { kind: "no_exhaustion" };
  const day = Math.ceil(limit / rate);
  if (day > daysInMonth) return { kind: "no_exhaustion" };
  return { kind: "exhaustion", day };
}

/** Текст прогноза по одному измерению (Г1 — текст, без графиков). */
export function forecastText(forecast: BudgetForecast): string {
  switch (forecast.kind) {
    case "insufficient_data":
      return "недостаточно данных";
    case "no_exhaustion":
      return "при текущем темпе не исчерпается до конца месяца";
    case "exhaustion":
      return `при текущем темпе лимит будет исчерпан к дню ${forecast.day}`;
  }
}
