"use client";

import { RouteError } from "@/components/ui/route-error";

/**
 * Error boundary for the authenticated dashboard subtree (v50). A render error
 * here (e.g. a failed data fetch that throws) shows a graceful, retryable
 * boundary instead of a crash. No internal detail is leaked (see RouteError).
 */
export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError error={error} reset={reset} surface="dashboard" />;
}
