"use client";

import { RouteError } from "@/components/ui/route-error";

/**
 * Error boundary for the public marketing subtree (v50). Keeps the landing /
 * pricing / docs pages graceful on an unexpected render error.
 */
export default function MarketingError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError error={error} reset={reset} surface="page" />;
}
