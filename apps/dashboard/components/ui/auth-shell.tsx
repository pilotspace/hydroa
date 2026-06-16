import * as React from "react";
import { Hexagon } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * AuthShell — the v23 enterprise split-screen frame for the public auth pages
 * (/login, /signup).
 *
 * Left: a DECORATIVE brand panel (the "Hydroa" wordmark + Hexagon mark + tagline).
 * It is marked `data-slot="auth-brand"`, `aria-hidden="true"`, carries NO heading,
 * landmark element, or focusable child, and is `hidden lg:flex` — so it is pure
 * chrome on desktop and absent on mobile. Because jsdom has no CSS engine, the
 * `aria-hidden` is what actually keeps it out of the a11y tree in tests; in real
 * Chromium axe likewise skips an aria-hidden subtree (incl. color-contrast).
 *
 * Right: the `<main>` content column — the SOLE landmark — which renders the page's
 * own `<h1>` + form as children. AuthShell owns the single `<main>` so each page
 * keeps exactly one main + one form landmark.
 */
export interface AuthShellProps {
  children: React.ReactNode;
  className?: string;
}

export function AuthShell({ children, className }: AuthShellProps) {
  return (
    <div className={cn("grid min-h-screen lg:grid-cols-2", className)}>
      {/* Decorative brand panel — removed from the a11y tree, no focusable/heading children. */}
      <div
        data-slot="auth-brand"
        aria-hidden="true"
        className="hidden flex-col justify-between bg-primary p-10 text-primary-foreground lg:flex"
      >
        <div className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Hexagon className="size-6" />
          <span>Hydroa</span>
        </div>
        <div className="space-y-3">
          <p className="text-2xl font-semibold leading-snug">
            The enterprise gateway for every AI model.
          </p>
          <p className="text-sm text-primary-foreground/90">
            One API, governed access, and unified spend across OpenAI, Anthropic, Gemini,
            Bedrock, and Azure.
          </p>
        </div>
        <p className="text-xs text-primary-foreground/80">© Hydroa. All rights reserved.</p>
      </div>

      {/* Content column — the single <main> landmark; hosts the page heading + form. */}
      <main className="flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-sm space-y-6">{children}</div>
      </main>
    </div>
  );
}
