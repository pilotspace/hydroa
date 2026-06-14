import * as React from "react";
import { BarChart3, Boxes, KeyRound, Receipt, Users } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * The responsive, accessible application shell every dashboard surface inherits.
 * a11y contract (WCAG 2.2 AA): a skip-link to #main as the FIRST focusable element,
 * a Primary <nav> landmark, and a <main id="main"> landmark. Responsive: the nav is
 * a fixed sidebar from the `lg` breakpoint up and a top bar below it.
 */

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/usage", label: "Usage", icon: BarChart3 },
  { href: "/spend", label: "Spend", icon: Receipt },
  { href: "/keys", label: "API Keys", icon: KeyRound },
  { href: "/models", label: "Models", icon: Boxes },
  { href: "/teams", label: "Teams", icon: Users },
];

export interface AppShellProps {
  children: React.ReactNode;
  /** The active route path, used to mark the current nav item. */
  activePath?: string;
}

export function AppShell({ children, activePath }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Skip to main content
      </a>

      <div className="flex min-h-screen flex-col lg:flex-row">
        <nav
          aria-label="Primary"
          className="flex shrink-0 gap-1 border-b border-border bg-card p-3 lg:w-60 lg:flex-col lg:border-b-0 lg:border-r"
        >
          <div className="mb-2 hidden px-2 text-lg font-semibold tracking-tight text-foreground lg:block">
            Hydroa
          </div>
          {NAV_ITEMS.map((item) => {
            const isActive = activePath === item.href;
            const Icon = item.icon;
            return (
              <a
                key={item.href}
                href={item.href}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className="size-4" />
                {item.label}
              </a>
            );
          })}
        </nav>

        <main id="main" className="flex-1 p-4 lg:p-8">
          {children}
        </main>
      </div>
    </div>
  );
}
