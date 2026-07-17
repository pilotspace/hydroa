"use client";

import * as React from "react";
import { PanelLeft } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * AppSidebar parts — the reusable, token-driven building blocks for the v23 enterprise
 * navigation rail (brand header · grouped nav · user footer · collapse trigger). These are
 * presentational; the live shell (app-shell-sidebar) composes + wires them with real nav
 * data, role filtering, and the mobile sheet. Consumes the v23 `--sidebar-*` tokens only.
 */

export interface SidebarProps extends React.HTMLAttributes<HTMLElement> {
  /** Accessible name for the navigation landmark. */
  "aria-label"?: string;
}

export const Sidebar = React.forwardRef<HTMLElement, SidebarProps>(
  ({ className, "aria-label": ariaLabel = "Primary", ...props }, ref) => (
    <nav
      ref={ref}
      aria-label={ariaLabel}
      className={cn(
        "flex h-full w-[264px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
        className,
      )}
      {...props}
    />
  ),
);
Sidebar.displayName = "Sidebar";

export function SidebarHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-center justify-between gap-2 border-b border-sidebar-border p-3", className)}
      {...props}
    />
  );
}

export function SidebarBrand({
  title,
  icon,
  className,
}: {
  title: string;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground", className)}>
      {icon ? (
        // Brand mark — a Classic-Blue gradient tile (token utilities only, no raw hex → R3-safe).
        // The deep→bright blue gradient is the shared, always-present brand moment app-wide.
        <span
          className="inline-flex size-7 items-center justify-center rounded-md bg-gradient-to-br from-brand-from to-brand-to text-white shadow-sm"
          aria-hidden="true"
        >
          {icon}
        </span>
      ) : null}
      <span>{title}</span>
    </div>
  );
}

export function SidebarContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex-1 overflow-y-auto p-2", className)} {...props} />;
}

export function SidebarGroup({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("py-2", className)} {...props} />;
}

export function SidebarGroupLabel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      // Quiet section header on the light rail: muted-foreground (slate-600, AA on the light
      // surfaces per the W1 contrast fix); uppercase + tracking + semibold reads as a label.
      className={cn("px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground", className)}
      {...props}
    />
  );
}

export interface SidebarItemProps {
  href: string;
  icon?: React.ReactNode;
  active?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function SidebarItem({ href, icon, active, children, className }: SidebarItemProps) {
  return (
    <a
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        // Active = azure-tinted accent-soft fill + AA-safe deep-azure text (no left accent bar —
        // matches the captured artifact, which uses fill + colored text only). text-accent-soft-
        // foreground, NOT text-primary — plain --primary is only 4.1:1 on accent-soft (fails AA).
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150 ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        active
          ? "bg-accent-soft text-accent-soft-foreground font-semibold"
          : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
        className,
      )}
    >
      {icon ? (
        <span className="size-4 shrink-0" aria-hidden="true">
          {icon}
        </span>
      ) : null}
      {children}
    </a>
  );
}

export function SidebarFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("border-t border-sidebar-border p-3", className)} {...props} />;
}

export interface SidebarTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  className?: string;
}

export function SidebarTrigger({ className, ...props }: SidebarTriggerProps) {
  return (
    <button
      type="button"
      // Single source of truth for this icon-only control's accessible name (it renders only a
      // <PanelLeft> glyph). Placed BEFORE {...props} on purpose: it is the default, but a consumer
      // MAY override it with an explicit aria-label when context needs a more specific name.
      aria-label="Toggle sidebar"
      title="Toggle sidebar"
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-md text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        className,
      )}
      {...props}
    >
      <PanelLeft className="size-4" aria-hidden="true" />
    </button>
  );
}
