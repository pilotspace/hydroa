"use client";

/**
 * DashboardShell — the client wrapper that feeds the current user's role into the
 * (presentational) AppShell so the Primary nav can hide admin-only links from a
 * `member`.
 *
 * It reuses the existing `["current-user"]` query (useCurrentUser → GET
 * /api/auth/me) — NO new network call. While the identity is loading or if it
 * fails, role is null and AppShell FAILS OPEN (all links shown); the gateway still
 * enforces RBAC on navigate, so this is a pure UX affordance, never a lockout.
 */

import { AppShell } from "@/components/ui";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

export interface DashboardShellProps {
  children: React.ReactNode;
  /** The active route path, forwarded to AppShell for aria-current marking. */
  activePath?: string;
}

export function DashboardShell({ children, activePath }: DashboardShellProps) {
  const { data } = useCurrentUser();
  return (
    <AppShell role={data?.role ?? null} activePath={activePath}>
      {children}
    </AppShell>
  );
}
