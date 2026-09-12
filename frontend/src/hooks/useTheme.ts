import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "orqion-theme";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") {
    return "light";
  }
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    return stored;
  }
  if (typeof window.matchMedia === "function") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "light";
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

export function useTheme() {
  const [theme, applyState] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const toggle = () => {
    applyState((prev) => (prev === "light" ? "dark" : "light"));
  };

  // Выбор конкретного варианта, а не переключение: в разделе «Профиль» тема
  // выбирается из списка, где видно оба значения.
  const setTheme = (next: Theme) => {
    applyState(next);
  };

  return { theme, toggle, setTheme };
}
