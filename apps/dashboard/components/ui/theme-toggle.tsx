"use client";

import * as React from "react";
import { Moon, Sun, Laptop } from "lucide-react";
import { cn } from "@/lib/cn";
import { useTheme, type Theme } from "./theme-provider";

/**
 * ThemeToggle — a single keyboard-operable button that cycles light → dark → system.
 * The accessible name always names the current mode; the icon (aria-hidden) reflects the
 * RESOLVED theme so it tracks the OS while in "system".
 */
const NEXT: Record<Theme, Theme> = { light: "dark", dark: "system", system: "light" };

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const Icon = theme === "system" ? Laptop : resolvedTheme === "dark" ? Moon : Sun;
  return (
    <button
      type="button"
      onClick={() => setTheme(NEXT[theme])}
      aria-label={`Toggle theme (current: ${theme})`}
      title="Toggle theme"
      className={cn(
        "inline-flex size-9 items-center justify-center rounded-md border border-border bg-card text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
