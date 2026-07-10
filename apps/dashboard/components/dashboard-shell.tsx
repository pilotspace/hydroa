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
 *
 * impersonation-ui TASK.md §3 CONTRACT M7 — additive: a SEPARATE useImpersonationStatus()
 * call (never touches the useCurrentUser() call/props above — role/userEmail stay anchored
 * to the REAL superadmin throughout an active session, per §1 Framings) decides whether to
 * pass <ImpersonationBanner/> as AppShell's new `banner` prop. Passing `undefined` (never a
 * banner element that would itself render null) while inactive matters: AppShell's own M6
 * height-class swap keys off whether `banner` is present at all, not what it renders — an
 * always-truthy banner prop would permanently reserve the calc-based height for a banner
 * that's usually invisible.
 *
 * command-palette TASK.md §3 CONTRACT — additive: mirrors the `banner` recipe above
 * byte-for-byte in shape. `PlatformCommandPalette` is passed as AppShell's new
 * `commandPalette` prop if and only if `data?.role === "superadmin"` EXACTLY — computed
 * from the SAME useCurrentUser() call already made here (no new network request). This is
 * the UI-layer, fail-CLOSED mount gate (M1/R2): `undefined` (never a component that itself
 * renders null) for every other role, including a still-loading/null identity — mirrors
 * app-shell.tsx's own fail-CLOSED `showPlatformNav(role) => role === "superadmin"` reasoning
 * (the palette's very EXISTENCE in the tree is the risk, not just its data). The backend's
 * own unmodified `require_superadmin` / `authorize_tenant_scope` gates remain the
 * authoritative layer underneath — this is defense-in-depth, never a replacement.
 */

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { AppShell } from "@/components/ui";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { useImpersonationStatus } from "@/lib/hooks/use-impersonation-status";
import { ImpersonationBanner } from "@/components/platform/ImpersonationBanner";
import { PlatformCommandPalette } from "@/components/platform/PlatformCommandPalette";

export interface DashboardShellProps {
  children: ReactNode;
}

export function DashboardShell({ children }: DashboardShellProps) {
  const pathname = usePathname();
  const { data } = useCurrentUser();
  const impersonation = useImpersonationStatus();
  return (
    <AppShell
      activePath={pathname ?? undefined}
      role={data?.role ?? null}
      userEmail={data?.email ?? null}
      banner={impersonation.data?.active ? <ImpersonationBanner /> : undefined}
      commandPalette={data?.role === "superadmin" ? <PlatformCommandPalette /> : undefined}
    >
      {children}
    </AppShell>
  );
}
