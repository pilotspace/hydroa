"use client";

/**
 * JoinedWorkspaceCallout — the polished "you joined {tenant}" onboarding
 * confirmation (domain-claims-console TASK.md §3 CONTRACT — FROZEN @ v1, M6/R3;
 * consumes task-2 domain-auto-assign-login's shipped signal).
 *
 * Shown IFF the advisory join signal is present: `?joined=1` on the post-login
 * landing URL (forwarded by the SSO callbacks). READ-ONLY celebration (R3): it
 * never mutates server state, never re-POSTs, never influences routing; dismiss
 * is local component state only. Absent the signal it renders NOTHING and fires
 * NO fetch — the session-context read lives in an inner component that only
 * mounts once the signal is confirmed, so a signal-less landing costs zero IO.
 *
 * The tenant display name comes from the already-loaded session context
 * (useCurrentUser -> GET /api/auth/me, an additive `tenant_name` relay of the
 * gateway's verified MeResponse — Tin-confirmed source; no extra fetch beyond
 * the shared, cached current-user query). If the name is absent (older gateway,
 * degraded relay) the copy falls back to a generic "an existing workspace" —
 * fail-closed on identity data, never a crash (frozen §3 mitigation).
 *
 * The celebration marker reuses the seal idiom (Badge + Lock + sr-only
 * assertion — the InvoiceStatusSeal / DomainStatusSeal family), per M6.
 */

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Lock, X } from "lucide-react";
import { Badge } from "@/components/ui";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

export function JoinedWorkspaceCallout() {
  const searchParams = useSearchParams();
  const joined = searchParams.get("joined") === "1";

  // R3: no signal -> nothing renders AND nothing fetches (the inner component,
  // which owns the session-context query, never mounts).
  if (!joined) return null;
  return <JoinedCalloutBody />;
}

function JoinedCalloutBody() {
  const [dismissed, setDismissed] = useState(false);
  const { data, isLoading } = useCurrentUser();

  if (dismissed || isLoading) return null;

  const tenantName = data?.tenant_name?.trim();
  const message = tenantName
    ? `You joined the ${tenantName} workspace`
    : "You joined an existing workspace";

  return (
    <div
      role="status"
      data-slot="joined-workspace-callout"
      className="flex items-start justify-between gap-3 rounded-lg border border-success/30 bg-success/10 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success" className="gap-1">
          <Lock className="size-3" aria-hidden="true" />
          Joined
          <span className="sr-only"> — workspace membership confirmed</span>
        </Badge>
        <p className="text-sm text-foreground">{message}.</p>
      </div>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => setDismissed(true)}
        className="rounded-sm text-muted-foreground opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}
