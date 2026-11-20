export type Theme = "light" | "dark" | "system";
const key = "undercover-appearance";

export function preferredTheme(): Theme {
  const value = localStorage.getItem(key);
  return value === "light" || value === "dark" ? value : "system";
}

export function applyTheme() {
  const chosen = preferredTheme();
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
  document.documentElement.dataset.theme =
    chosen === "system" ? systemTheme : chosen;
}

export function chooseTheme(theme: Theme) {
  localStorage.setItem(key, theme);
  applyTheme();
}

export function watchTheme() {
  applyTheme();
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  query.addEventListener("change", applyTheme);
  window.addEventListener("storage", applyTheme);
  return () => {
    query.removeEventListener("change", applyTheme);
    window.removeEventListener("storage", applyTheme);
  };
}
