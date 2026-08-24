import { describe, it, expect } from "vitest";
import {
  MIN_FORECAST_DAYS,
  forecastDimension,
  forecastText,
  monthProgress,
} from "./budgetForecast";

/**
 * T-441: линейная экстраполяция текущего месяца (А1), измерения
 * независимо (В3), деградация «< 3 дней → недостаточно данных».
 */

describe("forecastDimension", () => {
  it("вырожденный лимит (0 / отрицательный / null) — прогноза нет", () => {
    // Ключевой регресс: cost_month=0 у пресетов — НЕ «исчерпан сегодня».
    expect(forecastDimension(500, 0, 10, 31)).toBeNull();
    expect(forecastDimension(500, -1, 10, 31)).toBeNull();
    expect(forecastDimension(500, null, 10, 31)).toBeNull();
    expect(forecastDimension(500, undefined, 10, 31)).toBeNull();
  });

  it("< 3 дней месяца — недостаточно данных даже при огромном темпе", () => {
    expect(forecastDimension(100000, 1000, MIN_FORECAST_DAYS - 1, 31)).toEqual({
      kind: "insufficient_data",
    });
    expect(forecastDimension(100000, 1000, 1, 31)).toEqual({
      kind: "insufficient_data",
    });
  });

  it("темп = 0 — не исчерпается до конца месяца", () => {
    expect(forecastDimension(0, 1000, 10, 31)).toEqual({ kind: "no_exhaustion" });
  });

  it("исчерпание внутри месяца — точный день", () => {
    // Темп 5/день, лимит 100 → день 20.
    expect(forecastDimension(50, 100, 10, 31)).toEqual({
      kind: "exhaustion",
      day: 20,
    });
  });

  it("округление вверх: нецелый день исчерпания", () => {
    // Темп 3.3/день, лимит 100 → 30.3 → ceil → день 31.
    expect(forecastDimension(33, 100, 10, 31)).toEqual({
      kind: "exhaustion",
      day: 31,
    });
  });

  it("день исчерпания за пределами месяца — не исчерпается", () => {
    // День 34 > 31 день месяца.
    expect(forecastDimension(30, 100, 10, 31)).toEqual({
      kind: "no_exhaustion",
    });
  });

  it("граница: день исчерпания = последний день месяца", () => {
    // Темп 5/день, лимит 150 → ровно день 30 из 30.
    expect(forecastDimension(50, 150, 10, 30)).toEqual({
      kind: "exhaustion",
      day: 30,
    });
  });

  it("лимит уже превышен — день исчерпания в прошлом (честная экстраполяция)", () => {
    // Использовано 120% лимита за 20 дней → исчерпан к дню ceil(20/1.2)=17.
    expect(forecastDimension(1200, 1000, 20, 31)).toEqual({
      kind: "exhaustion",
      day: 17,
    });
  });
});

describe("monthProgress", () => {
  it("считает прошедшие дни (включая сегодня) и длину месяца", () => {
    expect(monthProgress(new Date(2026, 7, 25))).toEqual({
      daysElapsed: 25,
      daysInMonth: 31,
    });
  });

  it("февраль високосного года", () => {
    expect(monthProgress(new Date(2028, 1, 10))).toEqual({
      daysElapsed: 10,
      daysInMonth: 29,
    });
  });
});

describe("forecastText", () => {
  it("тексты всех исходов", () => {
    expect(forecastText({ kind: "insufficient_data" })).toBe("недостаточно данных");
    expect(forecastText({ kind: "no_exhaustion" })).toBe(
      "при текущем темпе не исчерпается до конца месяца",
    );
    expect(forecastText({ kind: "exhaustion", day: 27 })).toBe(
      "при текущем темпе лимит будет исчерпан к дню 27",
    );
  });
});
