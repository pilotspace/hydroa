"use client";

/**
 * PlatformSafetyBanner — the persistent cross-tenant safety cue (admin-console-ui,
 * TASK.md M5). Mounted ONCE at the tenant-detail route-shell level (never per-tab),
 * so it never re-fetches or re-mounts while a superadmin switches between Config /
 * Budget / Keys / Members (M4, M5).
 *
 * A new COMPOSITION built entirely from existing tokens (bg-warning tint +
 * text-warning[-foreground] + a lucide icon, aria-hidden) — no new primitive is
 * added to components/ui/ for a single caller (§3 CONTRACT).
 *
 * The tenant name NEVER truncates here, regardless of length — unlike the
 * directory table's Name cell, which may truncate with a native `title` tooltip
 * (M5's own explicit carve-out: identity-critical text is never clipped on this
 * surface, wraps onto multiple lines instead).
 */

import { ShieldAlert } from "lucide-react";
import { cn } from "@/lib/cn";

export interface PlatformSafetyBannerProps {
  /** The target tenant's full, un-truncated name. */
  tenantName: string;
  /** TenantSummaryResponse.kind — "platform" is the superadmin's own reserved tenant. */
  kind: string;
  className?: string;
}

export function PlatformSafetyBanner({ tenantName, kind, className }: PlatformSafetyBannerProps) {
  const isOwnReservedTenant = kind === "platform";

  return (
    <div
      data-testid="platform-safety-banner"
      className={cn(
        "flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning-foreground",
        className,
      )}
    >
      <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
      {/* No `truncate` anywhere in this subtree — identity-critical text wraps
          instead of clipping, regardless of tenant-name length (M5). */}
      <p className="font-medium">
        {isOwnReservedTenant ? (
          <>Viewing: {tenantName} (your own reserved tenant) — Platform Admin Mode</>
        ) : (
          <>Viewing: {tenantName} — Platform Admin Mode</>
        )}
      </p>
    </div>
  );
}
