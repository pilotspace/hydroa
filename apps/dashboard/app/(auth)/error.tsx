"use client";

import { RouteError } from "@/components/ui/route-error";

/**
 * Error boundary for the auth subtree (login / signup) (v50). Form-level errors
 * are handled inline by the forms; this catches unexpected render failures.
 */
export default function AuthError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError error={error} reset={reset} surface="sign-in page" />;
}
