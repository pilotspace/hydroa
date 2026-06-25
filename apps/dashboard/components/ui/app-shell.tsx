"use client";

import * as React from "react";
import { Activity, BarChart3, Bell, Boxes, ClipboardList, GaugeCircle, HeartPulse, Hexagon, KeyRound, Menu, Receipt, Settings, Users } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  Sidebar,
  SidebarBrand,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarHeader,
  SidebarItem,
  SidebarTrigger,
} from "./sidebar";
import { ThemeToggle } from "./theme-toggle";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "./dialog";

/**
 * The responsive, accessible application shell every dashboard surface inherits — rebuilt
 * (v23) on the token-driven Sidebar parts: a branded, collapsible desktop rail; a mobile
 * sheet (hamburger → Dialog); a theme toggle; and a user-identity footer.
 *
 * The frozen v13 shell contract is preserved EXACTLY: a skip-link to #main as the FIRST
 * focusable element, a single Primary <nav> landmark (the desktop rail — the mobile sheet's
 * nav is labelled distinctly and only mounted when open), a <main id="main"> landmark, and a
 * responsive `lg:flex-row` root (stacked on mobile → row from the lg breakpoint).
 */

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /**
   * Minimum role that can use the page the link points to. `"admin"` means the
   * page's GET 403s on a `member` (owner|admin only) — so the link is hidden from
   * members. Omitted ⇒ any authenticated role may see the link. The gateway remains
   * the source of truth — this is UX-only and FAILS OPEN.
   */
  minRole?: "admin";
}

const NAV_ITEMS: NavItem[] = [
  { href: "/app/usage", label: "Usage", icon: BarChart3 },
  { href: "/app/spend", label: "Spend", icon: Receipt },
  { href: "/app/keys", label: "API Keys", icon: KeyRound },
  { href: "/app/models", label: "Models", icon: Boxes, minRole: "admin" },
  { href: "/app/teams", label: "Teams", icon: Users, minRole: "admin" },
  { href: "/app/members", label: "Members", icon: Users, minRole: "admin" },
  { href: "/app/routing", label: "Routing", icon: Activity, minRole: "admin" },
  { href: "/app/alerts", label: "Alerts", icon: Bell, minRole: "admin" },
  { href: "/app/audit", label: "Audit", icon: ClipboardList, minRole: "admin" },
  { href: "/app/health", label: "Health", icon: HeartPulse, minRole: "admin" },
  { href: "/app/slo", label: "SLO", icon: GaugeCircle, minRole: "admin" },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

const BRAND = "Hydroa";

export interface AppShellProps {
  children: React.ReactNode;
  /** The active route path, used to mark the current nav item. */
  activePath?: string;
  /**
   * The current user's role (owner|admin|member). When `"member"`, admin-only
   * links are hidden. Any other value — including null/undefined while the
   * identity is still loading or failed — FAILS OPEN (all links shown); the
   * gateway still enforces RBAC on navigate, so no one is locked out of their nav.
   */
  role?: string | null;
  /** The signed-in user's email, shown in the sidebar footer when present. */
  userEmail?: string | null;
}

function visibleItems(role?: string | null): NavItem[] {
  return NAV_ITEMS.filter((item) => !(item.minRole === "admin" && role === "member"));
}

/**
 * Render the nav links. When `collapsed`, the label text is visually hidden (sr-only) but
 * REMAINS the link's accessible name — an item is never reduced to an icon without a name.
 */
function NavLinks({
  items,
  activePath,
  collapsed,
}: {
  items: NavItem[];
  activePath?: string;
  collapsed: boolean;
}) {
  return (
    <>
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <SidebarItem
            key={item.href}
            href={item.href}
            active={activePath === item.href}
            icon={<Icon className="size-4" />}
          >
            <span className={collapsed ? "sr-only" : undefined}>{item.label}</span>
          </SidebarItem>
        );
      })}
    </>
  );
}

export function AppShell({ children, activePath, role, userEmail }: AppShellProps) {
  const [collapsed, setCollapsed] = React.useState(false);
  const items = visibleItems(role);
  const brandIcon = <Hexagon className="size-5" />;

  return (
    <div className="min-h-screen bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Skip to main content
      </a>

      <Dialog>
        <div className="flex min-h-screen flex-col lg:flex-row">
          {/* Mobile header — visible below the lg breakpoint; opens the nav sheet. */}
          <header className="flex items-center justify-between gap-2 border-b border-border bg-card p-3 lg:hidden">
            <SidebarBrand title={BRAND} icon={brandIcon} />
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <DialogTrigger asChild>
                <button
                  type="button"
                  aria-label="Open navigation"
                  className="inline-flex size-9 items-center justify-center rounded-md border border-border bg-card text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Menu className="size-4" aria-hidden="true" />
                </button>
              </DialogTrigger>
            </div>
          </header>

          {/* Desktop rail — the single Primary nav landmark; collapsible from the lg breakpoint up. */}
          <Sidebar
            aria-label="Primary"
            data-state={collapsed ? "collapsed" : "expanded"}
            className={cn("hidden lg:flex", collapsed ? "w-16" : "w-64")}
          >
            <SidebarHeader className={collapsed ? "justify-center" : undefined}>
              {collapsed ? null : <SidebarBrand title={BRAND} icon={brandIcon} />}
              <SidebarTrigger
                aria-expanded={!collapsed}
                onClick={() => setCollapsed((c) => !c)}
              />
            </SidebarHeader>
            <SidebarContent>
              <SidebarGroup>
                <NavLinks items={items} activePath={activePath} collapsed={collapsed} />
              </SidebarGroup>
            </SidebarContent>
            <SidebarFooter>
              <div className="flex items-center justify-between gap-2">
                {userEmail ? (
                  <div className={cn("min-w-0", collapsed && "sr-only")}>
                    <div className="truncate text-sm font-medium text-sidebar-foreground">{userEmail}</div>
                    {role ? (
                      <div className="truncate text-xs capitalize text-muted-foreground">{role}</div>
                    ) : null}
                  </div>
                ) : (
                  <span className={cn("text-xs text-muted-foreground", collapsed && "sr-only")}>
                    Not signed in
                  </span>
                )}
                <ThemeToggle />
              </div>
            </SidebarFooter>
          </Sidebar>

          <main id="main" className="flex-1 p-4 lg:p-8">
            {children}
          </main>
        </div>

        {/* Mobile nav sheet — portal-mounted only when open, so the default render keeps ONE
            Primary landmark. Its nav is labelled "Site" (distinct from the rail's "Primary"). */}
        <DialogContent className="left-0 top-0 h-full max-w-xs translate-x-0 translate-y-0 rounded-none rounded-r-lg sm:max-w-xs">
          <DialogTitle>Navigation</DialogTitle>
          <DialogDescription className="sr-only">Primary site navigation</DialogDescription>
          <nav aria-label="Site" className="mt-2 flex flex-col gap-1">
            <NavLinks items={items} activePath={activePath} collapsed={false} />
          </nav>
        </DialogContent>
      </Dialog>
    </div>
  );
}
