/**
 * lib/hooks/use-current-user.ts
 *
 * Fetches user claims from GET /api/auth/me.
 * Returns { data, isLoading, isError } — no JWT decode client-side.
 * No localStorage access.
 */

"use client";

import { useQuery } from "@tanstack/react-query";

export interface CurrentUser {
  user_id: string | null;
  tenant_id: string | null;
  email: string | null;
  role: string | null;
  exp: number | null;
  /**
   * domain-claims-console M6 (additive): the caller's OWN tenant display name,
   * relayed by /api/auth/me from the gateway's verified MeResponse. OPTIONAL —
   * the BFF only forwards it when the verified upstream response carries it
   * (older gateway → absent); consumers must fall back to generic copy.
   */
  tenant_name?: string | null;
}

function appBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return (
      process.env.NEXT_PUBLIC_APP_URL ??
      "http://localhost:3000"
    );
  }
  return "";
}

async function fetchCurrentUser(): Promise<CurrentUser> {
  const res = await fetch(`${appBase()}/api/auth/me`, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch current user: ${res.status}`);
  }

  return res.json() as Promise<CurrentUser>;
}

export function useCurrentUser(): {
  data: CurrentUser | null;
  isLoading: boolean;
  isError: boolean;
} {
  const { data, isLoading, isError } = useQuery<CurrentUser>({
    queryKey: ["current-user"],
    queryFn: fetchCurrentUser,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  return {
    data: data ?? null,
    isLoading,
    isError,
  };
}
