"use client";

/**
 * ImpersonationBanner — the persistent, shell-level "you are impersonating" signal
 * (impersonation-ui TASK.md §3 CONTRACT Part C, M8). Self-contained: reads
 * useImpersonationStatus() itself, no props. Renders null when inactive. Its End
 * control mirrors PlatformKeysTab's own role="dialog" + useFocusTrap revoke-confirm
 * pattern exactly (see tests/platform-keys.test.tsx's
 * test_revoke_key_requires_focus_trapped_confirm) — same overlay/dialog shape,
 * same Confirm/Cancel button pair, same shared Button component + variants.
 */

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui";
import { resilientFetch } from "@/lib/resilient-fetch";
import { useFocusTrap } from "@/lib/use-focus-trap";
import { useImpersonationStatus } from "@/lib/hooks/use-impersonation-status";

function appBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  }
  return "";
}

async function endImpersonationSession(sessionId: string): Promise<void> {
  const res = await resilientFetch(
    `${appBase()}/api/platform/impersonation/sessions/${sessionId}`,
    { method: "DELETE", credentials: "include" },
  );
  if (res.status === 204) return;
  let title = "Failed to end impersonation";
  try {
    const body = (await res.json()) as { title?: string };
    if (body?.title) title = body.title;
  } catch {
    // keep the default title
  }
  throw new Error(title);
}

/** mm:ss (or m:ss) countdown derived from a Date.now() diff — recomputed on each
 * display tick, never re-fetched more often than useImpersonationStatus()'s own
 * ~5s refetchInterval (M8). */
function formatCountdown(msRemaining: number): string {
  const totalSeconds = Math.max(0, Math.floor(msRemaining / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function ImpersonationBanner() {
  const queryClient = useQueryClient();
  const { data } = useImpersonationStatus();
  const active = data?.active === true;

  const [now, setNow] = React.useState(() => Date.now());
  const [confirmOpen, setConfirmOpen] = React.useState(false);
  const [endError, setEndError] = React.useState<string | null>(null);

  // Live display tick — only while actually active, so an inactive/unmounted
  // banner never runs a background timer.
  React.useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  const endMutation = useMutation({
    mutationFn: (sessionId: string) => endImpersonationSession(sessionId),
    onSuccess: () => {
      setConfirmOpen(false);
      setEndError(null);
      // M10: the FULL cache, not a hand-maintained key allowlist — the
      // deliberately blunt instrument against cross-boundary cache leakage.
      queryClient.clear();
    },
    onError: (err) => {
      setEndError(err instanceof Error ? err.message : "Failed to end impersonation");
    },
  });

  function handleConfirmEnd() {
    if (data?.active) {
      setEndError(null);
      endMutation.mutate(data.session_id);
    }
  }
  function handleCancelEnd() {
    setConfirmOpen(false);
    setEndError(null);
  }
  const trapRef = useFocusTrap<HTMLDivElement>(confirmOpen, handleCancelEnd);

  if (!data?.active) {
    return null;
  }

  const msRemaining = data.expires_at - now;

  return (
    <div
      data-testid="impersonation-banner"
      className="flex items-center justify-between gap-3 border-b border-border bg-warning/10 px-4 py-2 text-sm text-warning-foreground"
    >
      <div className="flex min-w-0 items-center gap-2">
        <ShieldAlert className="size-4 shrink-0 text-warning" aria-hidden="true" />
        <p className="min-w-0 truncate">
          Impersonating <span className="font-medium">{data.target.email}</span> (
          <span className="capitalize">{data.target.role}</span>)
          {data.actor ? <> — signed in as {data.actor.email}</> : null}
          {" · "}
          <span className="font-mono tabular-nums">{formatCountdown(msRemaining)} remaining</span>
        </p>
      </div>
      <Button type="button" variant="outline" size="sm" onClick={() => setConfirmOpen(true)}>
        End impersonation
      </Button>

      {confirmOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="impersonation-end-overlay"
        >
          <div
            ref={trapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm end impersonation"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              End this impersonation session and return to your own identity?
            </p>
            {endError && (
              <p role="alert" aria-live="polite" className="mt-2 text-sm text-destructive">
                {endError}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="destructive"
                disabled={endMutation.isPending}
                onClick={handleConfirmEnd}
              >
                Confirm
              </Button>
              <Button type="button" variant="outline" onClick={handleCancelEnd}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
