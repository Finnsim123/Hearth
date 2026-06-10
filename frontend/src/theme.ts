/**
 * Three-way theme switcher: "system" (default) | "light" | "dark".
 * "system" removes the data-theme attribute so CSS follows
 * prefers-color-scheme; explicit modes pin it. Persisted in localStorage.
 */
export type ThemeMode = "system" | "light" | "dark";

const KEY = "hearth.theme";

export function getTheme(): ThemeMode {
  const v = localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "system";
}

export function applyTheme(mode: ThemeMode): void {
  if (mode === "system") {
    document.documentElement.removeAttribute("data-theme");
    localStorage.removeItem(KEY);
  } else {
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem(KEY, mode);
  }
}

export function cycleTheme(): ThemeMode {
  const order: ThemeMode[] = ["system", "light", "dark"];
  const next = order[(order.indexOf(getTheme()) + 1) % order.length];
  applyTheme(next);
  return next;
}

/** Call once at boot (before first paint if possible). */
export function initTheme(): void {
  applyTheme(getTheme());
}
