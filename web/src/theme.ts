// Theme helpers: class-based dark mode persisted in localStorage.

const THEME_KEY = "wiwi.theme";

export type Theme = "dark" | "light";

export function getTheme(): Theme {
  return (localStorage.getItem(THEME_KEY) as Theme) ?? "dark";
}

export function applyTheme(theme: Theme): void {
  localStorage.setItem(THEME_KEY, theme);
  document.documentElement.classList.toggle("dark", theme === "dark");
}
