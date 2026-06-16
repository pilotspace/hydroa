"use client";

/**
 * DashboardShell — the client wrapper that wires the live dashboard into the (presentational)
 * AppShell: it marks the active route from the URL and feeds the current user's identity
 * (role → which nav links show; email → the sidebar footer).
 *
 * It reuses the existing `["current-user"]` query (useCurrentUser → GET /api/auth/me) — NO new
 * network call. While the identity is loading or if it fails, role is null and AppShell FAILS
 * OPEN (all links shown); the gateway still enforces RBAC on navigate, so this is a pure UX
 * affordance, never a lockout. The active path comes from next/navigation's usePathname().
 */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { AppShell } from "@/components/ui";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

export interface DashboardShellProps {
  children: ReactNode;
}

export function DashboardShell({ children }: DashboardShellProps) {
  const pathname = usePathname();
  const { data } = useCurrentUser();
  return (
    <AppShell activePath={pathname ?? undefined} role={data?.role ?? null} userEmail={data?.email ?? null}>
      {children}
    </AppShell>
  );
}
