"use client";

/**
 * lib/hooks/use-impersonation-status.ts
 *
 * Polls GET /api/platform/impersonation (impersonation-ui TASK.md §3 CONTRACT Part
 * C) via resilientFetch (M12 — this task's own new client calls use the same
 * timeout/retry/circuit-breaker core bff-client.ts already uses, unlike
 * useCurrentUser's bare fetch, which this task deliberately does not copy).
 * retry:false at the React Query layer + a bounded ~5s refetchInterval — no retry
 * storm stacked on top of resilientFetch's own already-bounded retries.
 *
 * Defensive fallback (beyond the contract's literal pseudocode, added at Build):
 * DashboardShell calls this hook unconditionally (M7), and two EXISTING tests
 * (tests/marketing-shell.test.tsx, tests/design-system/app-shell-sidebar.test.tsx)
 * render DashboardShell directly with useCurrentUser module-mocked away and no
 * QueryClientProvider ancestor at all — relying entirely on that mock to avoid
 * needing one. A plain useQuery() call there would throw ("No QueryClient set")
 * and regress both. The real app (app/providers.tsx's getQueryClient()) and every
 * new test this task adds always mount under a real provider, so this fallback
 * never fires there — enabled:false means it never even attempts a fetch, so the
 * only observable effect when there truly is no provider is a harmless
 * {active:false}-shaped default instead of a hard crash: this hook's own
 * fail-closed contract (M1's "never throws, degrades closed") extended to its own
 * plumbing, not just its cookie-parsing.
 */

import { useContext } from "react";
import { QueryClientContext, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getQueryClient } from "@/lib/query-client";
import { resilientFetch } from "@/lib/resilient-fetch";

export interface ImpersonationTarget {
  user_id: string;
  tenant_id: string;
  email: string;
  role: string;
}

export interface ImpersonationActor {
  email: string;
  role: string;
}

export type ImpersonationStatus =
  | { active: false }
  | {
      active: true;
      session_id: string;
      expires_at: number;
      target: ImpersonationTarget;
      actor: ImpersonationActor | null;
    };

/** Mirrors bff-client.ts / use-current-user.ts's own appBase() exactly: Node's
 * fetch (undici) rejects relative URLs, so an absolute base is required in
 * Node/SSR/tests; the browser uses a relative ("") base via window.location. */
function appBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  }
  return "";
}

async function fetchImpersonationStatus(): Promise<ImpersonationStatus> {
  const res = await resilientFetch(`${appBase()}/api/platform/impersonation`, {
    method: "GET",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    // The route itself never returns non-200 for a normal status check — it
    // self-heals to {active:false} instead (TASK.md §3 Part A). A non-200 here
    // would mean a transport-level problem; degrade to inactive rather than
    // surface a hard error for what is, at worst, a transient blip on a purely
    // informational poll — never block the rest of the dashboard from rendering.
    return { active: false };
  }
  return (await res.json()) as ImpersonationStatus;
}

export function useImpersonationStatus(): UseQueryResult<ImpersonationStatus> {
  const contextClient = useContext(QueryClientContext);
  return useQuery(
    {
      queryKey: ["impersonation-status"],
      queryFn: fetchImpersonationStatus,
      retry: false,
      refetchInterval: 5000,
      enabled: contextClient !== undefined,
    },
    contextClient ?? getQueryClient(),
  );
}
