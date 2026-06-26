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
        "flex h-full w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground",
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
        <span className="text-sidebar-ring" aria-hidden="true">
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
      className={cn("px-2 pb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground", className)}
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
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150 ease-standard focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        active
          ? "bg-accent-soft text-primary font-semibold ring-1 ring-inset ring-accent-soft-border"
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
