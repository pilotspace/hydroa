"use client";

import * as React from "react";
import { ErrorState } from "@/components/ui/states";

/**
 * RouteError — the shared, leak-guarded body for every App-Router error
 * boundary (v50 failure-state-segments). It composes the v13 `ErrorState`
 * (role="alert" + Retry) and is wired to Next's `reset()`.
 *
 * SECURITY (no-leak invariant): this NEVER renders `error.message` or the stack
 * — only generic, surface-aware copy plus the safe `error.digest` (a hash Next
 * emits for support correlation). We do not rely on Next's prod redaction.
 */
export interface RouteErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
  surface?: string;
}

export function RouteError({ error, reset, surface }: RouteErrorProps) {
  const where = surface ? `the ${surface}` : "this page";
  return (
    <div className="mx-auto flex max-w-md flex-col gap-3 p-6">
      <ErrorState
        title="Something went wrong"
        titleAs="h1"
        description={`We couldn't load ${where}. Please retry, or come back in a moment.`}
        onRetry={reset}
      />
      {error.digest ? (
        <p className="text-xs text-muted-foreground">Reference: {error.digest}</p>
      ) : null}
    </div>
  );
}
