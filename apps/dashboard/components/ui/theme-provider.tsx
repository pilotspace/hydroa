"use client";

import * as React from "react";

/**
 * ThemeProvider — the v23 light/dark/system theme seam.
 *
 * Class-based dark mode: when the resolved theme is dark, `class="dark"` is set on
 * <html>, activating the `.dark` token block in globals.css. The choice persists to
 * localStorage; under "system" it follows `prefers-color-scheme` live (matchMedia).
 *
 * No-flash: `themeScript(storageKey)` returns a tiny IIFE meant for the document <head>
 * (see app/layout.tsx) that sets the class BEFORE hydration, so the first paint already
 * matches the stored choice — React then syncs without a flash.
 */

export type Theme = "light" | "dark" | "system";
type ResolvedTheme = "light" | "dark";

interface ThemeContextValue {
  theme: Theme;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = React.createContext<ThemeContextValue>({
  theme: "system",
  resolvedTheme: "light",
  setTheme: () => {},
});

const isTheme = (v: unknown): v is Theme => v === "light" || v === "dark" || v === "system";

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** Same-tab notifier bus — localStorage writes don't fire `storage` in the writing tab. */
const themeBus = new Set<() => void>();
function notifyTheme() {
  themeBus.forEach((cb) => cb());
}

function readStoredTheme(storageKey: string): Theme | null {
  if (typeof window === "undefined") return null;
  const v = window.localStorage.getItem(storageKey);
  return isTheme(v) ? v : null;
}

export interface ThemeProviderProps {
  children: React.ReactNode;
  defaultTheme?: Theme;
  storageKey?: string;
}

export function ThemeProvider({
  children,
  defaultTheme = "system",
  storageKey = "theme",
}: ThemeProviderProps) {
  // Subscribe to every source that can change the resolved theme: cross-tab `storage`
  // events, the same-tab bus (setTheme), and the OS color-scheme media query.
  const subscribe = React.useCallback((onStoreChange: () => void) => {
    if (typeof window === "undefined") return () => {};
    const mql = window.matchMedia(DARK_QUERY);
    window.addEventListener("storage", onStoreChange);
    mql.addEventListener("change", onStoreChange);
    themeBus.add(onStoreChange);
    return () => {
      window.removeEventListener("storage", onStoreChange);
      mql.removeEventListener("change", onStoreChange);
      themeBus.delete(onStoreChange);
    };
  }, []);

  // useSyncExternalStore reads the live external state on every change WITHOUT calling
  // setState in an effect (lint-clean) and renders the server snapshot during hydration
  // (no mismatch — the no-flash <script> already set the class).
  const theme = React.useSyncExternalStore<Theme>(
    subscribe,
    () => readStoredTheme(storageKey) ?? defaultTheme,
    () => defaultTheme,
  );
  const resolvedTheme = React.useSyncExternalStore<ResolvedTheme>(
    subscribe,
    () => {
      const t = readStoredTheme(storageKey) ?? defaultTheme;
      if (t !== "system") return t;
      return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
    },
    () => (defaultTheme === "dark" ? "dark" : "light"),
  );

  // The only side effect: keep the document class in sync with the resolved theme.
  React.useEffect(() => {
    document.documentElement.classList.toggle("dark", resolvedTheme === "dark");
  }, [resolvedTheme]);

  const setTheme = React.useCallback(
    (next: Theme) => {
      if (typeof window !== "undefined") window.localStorage.setItem(storageKey, next);
      notifyTheme();
    },
    [storageKey],
  );

  const value = React.useMemo<ThemeContextValue>(
    () => ({ theme, resolvedTheme, setTheme }),
    [theme, resolvedTheme, setTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return React.useContext(ThemeContext);
}

// The pre-hydration no-flash script now lives in the non-"use client" module ./theme-script so a
// Server Component (app/layout.tsx) can render it. Re-exported here for backward-compatible imports.
export { themeScript } from "./theme-script";
