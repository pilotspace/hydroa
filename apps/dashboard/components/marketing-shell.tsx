/**
 * MarketingShell — the public site chrome for all marketing routes.
 *
 * Frozen contract (§3 v1):
 *   - Distinct from DashboardShell: no AppShell, no sidebar, no auth state
 *   - WCAG-AA: skip-link as first focusable element, header/nav/main/footer landmarks,
 *     skip-link href="#main" (children must render <main id="main">)
 *   - Reuses Hydroa design tokens from globals.css (--primary, --foreground, etc.)
 *   - Server Component: no "use client", no cookie read, no authenticated data fetch
 *
 * Structure:
 *   <a href="#main"> skip-link
 *   <header> brand + nav (Product · Pricing · Docs) + Log in / Sign up CTAs
 *   {children}  (typically <main id="main">…</main>)
 *   <footer> 3 link columns + copyright
 */

import Link from "next/link";
import type { ReactNode } from "react";

interface MarketingShellProps {
  children: ReactNode;
}

const NAV_LINKS = [
  { label: "Product", href: "/#product" },
  { label: "Pricing", href: "/#pricing" },
  { label: "Docs", href: "/#docs" },
] as const;

const FOOTER_COLUMNS = [
  {
    heading: "Product",
    links: [
      { label: "Features", href: "/#product" },
      { label: "Pricing", href: "/#pricing" },
      { label: "Docs", href: "/#docs" },
    ],
  },
  {
    heading: "Company",
    links: [
      { label: "Blog", href: "/blog" },
      { label: "Status", href: "/status" },
    ],
  },
  {
    heading: "Legal",
    links: [
      { label: "Privacy", href: "/legal/privacy" },
      { label: "Terms", href: "/legal/terms" },
      { label: "Security", href: "/legal/security" },
    ],
  },
] as const;

export function MarketingShell({ children }: MarketingShellProps) {
  const currentYear = new Date().getFullYear();

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      {/* Skip link — FIRST focusable element for keyboard/AT users */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Skip to main content
      </a>

      {/* Site header — banner landmark */}
      <header className="sticky top-0 z-40 border-b border-border bg-card/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
          {/* Brand */}
          <Link
            href="/"
            className="flex items-center gap-2 text-lg font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label="Hydroa home"
          >
            <span
              aria-hidden="true"
              className="inline-flex size-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground"
            >
              H
            </span>
            <span>Hydroa</span>
          </Link>

          {/* Primary nav */}
          <nav aria-label="Main" className="hidden items-center gap-6 md:flex">
            {NAV_LINKS.map(({ label, href }) => (
              <Link
                key={label}
                href={href}
                className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                {label}
              </Link>
            ))}
          </nav>

          {/* CTA buttons */}
          <div className="flex items-center gap-2">
            <Link
              href="/login"
              className="inline-flex h-9 items-center justify-center rounded-md border border-border bg-background px-4 text-sm font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              Log in
            </Link>
            <Link
              href="/signup"
              className="inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              Sign up
            </Link>
          </div>
        </div>
      </header>

      {/* Page content — children must render <main id="main"> for the skip-link */}
      {children}

      {/* Site footer — contentinfo landmark */}
      <footer className="mt-auto border-t border-border bg-card">
        <div className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8">
          <div className="grid grid-cols-1 gap-8 sm:grid-cols-3">
            {FOOTER_COLUMNS.map((col) => (
              <div key={col.heading}>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-foreground">
                  {col.heading}
                </h2>
                <ul role="list" className="mt-4 space-y-2">
                  {col.links.map(({ label, href }) => (
                    <li key={label}>
                      <Link
                        href={href}
                        className="text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                      >
                        {label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="mt-8 border-t border-border pt-8 text-center">
            <p className="text-sm text-muted-foreground">
              &copy; {currentYear} Hydroa. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
