"use client";

/**
 * PlatformMembersTab — Members tab (M9) of the tenant-detail screen: lists the
 * TARGET tenant's users with a per-row role-reassign selector, mirroring
 * MembersPage.tsx's list + self-guard EXACTLY. Unlike MembersPage.tsx (which
 * receives callerRole/callerUserId as props from its Server Component parent),
 * this tab only receives `tenantId` — the caller's own identity is fetched
 * internally via useCurrentUser() (GET /api/auth/me).
 *
 * The backend exposes only GET "" + PUT /{user_id}/role (platform_users_router.py,
 * TASK.md §0 GROUND) — no invite/remove routes — so TeamMembersPanel's
 * add/remove-by-email shape does not apply here; a superadmin may assign any
 * of the 6 tiers (always assignable — the escalation ceiling that limits an
 * ordinary owner/admin in MembersPage.tsx does not apply to a superadmin).
 *
 * impersonation-ui TASK.md §3 CONTRACT M9 — additive: a NEW optional `tenantKind`
 * prop and a new per-row "Impersonate" action, gated to render for NO row unless
 * `tenantKind === "customer"` EXACTLY (fails CLOSED on absent/unrecognized values —
 * this is what keeps tests/platform-members.test.tsx, which never passes this
 * prop, passing with zero changes), also hidden for the caller's own row and any
 * role==="superadmin" row (R3), and DISABLED with inline explanatory text while
 * useImpersonationStatus() reports an already-active session (R4). Confirming
 * calls the Mint mutation (POST /api/platform/impersonation) with this row's
 * {tenant_id: tenantId, user_id: row.id} — mirrors PlatformKeysTab's own
 * role="dialog" + useFocusTrap revoke-confirm pattern exactly.
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Button, Loading, ErrorState } from "@/components/ui";
import { DataTable } from "@/components/ui/data-table";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { useImpersonationStatus } from "@/lib/hooks/use-impersonation-status";
import { resilientFetch } from "@/lib/resilient-fetch";
import { useFocusTrap } from "@/lib/use-focus-trap";

export interface PlatformTenantUser {
  id: string;
  email: string;
  role: string;
}

interface UsersListResponse {
  users: PlatformTenantUser[];
}

const ALL_ROLES = ["owner", "admin", "operator", "billing_admin", "viewer", "member"] as const;

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

/** Mirrors bff-client.ts / use-current-user.ts's own appBase() exactly (Node's
 * fetch rejects relative URLs; the browser resolves a relative base itself). */
function appBase(): string {
  if (typeof process !== "undefined" && process.versions?.node) {
    return process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000";
  }
  return "";
}

async function mintImpersonation(tenantId: string, userId: string): Promise<void> {
  const res = await resilientFetch(`${appBase()}/api/platform/impersonation`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: tenantId, user_id: userId }),
  });
  if (res.ok) return;
  let title = "Failed to start impersonation";
  try {
    const body = (await res.json()) as { title?: string };
    if (body?.title) title = body.title;
  } catch {
    // keep the default title
  }
  throw new Error(title);
}

export interface PlatformMembersTabProps {
  tenantId: string;
  /** impersonation-ui M9 — absent/anything other than exactly "customer" hides the
   * new Impersonate action for every row (fails CLOSED). */
  tenantKind?: string;
}

export function PlatformMembersTab({ tenantId, tenantKind }: PlatformMembersTabProps) {
  const queryClient = useQueryClient();
  const { data: currentUser } = useCurrentUser();
  const impersonationStatus = useImpersonationStatus();
  const queryKey = React.useMemo(() => ["platform-tenant-users", tenantId], [tenantId]);

  const usersQuery = useQuery<UsersListResponse>({
    queryKey,
    queryFn: () => bffGet<UsersListResponse>(`/admin/platform/tenants/${tenantId}/users`),
    retry: false,
  });

  const [assignError, setAssignError] = React.useState<string | null>(null);
  const [impersonateTarget, setImpersonateTarget] = React.useState<PlatformTenantUser | null>(null);
  const [impersonateError, setImpersonateError] = React.useState<string | null>(null);

  const impersonateMutation = useMutation({
    mutationFn: (user: PlatformTenantUser) => mintImpersonation(tenantId, user.id),
    onSuccess: () => {
      setImpersonateTarget(null);
      setImpersonateError(null);
      // M10: the FULL cache, not a hand-maintained key allowlist — the
      // deliberately blunt instrument against cross-boundary cache leakage.
      queryClient.clear();
    },
    onError: (err) => {
      setImpersonateError(err instanceof Error ? err.message : "Failed to start impersonation");
    },
  });

  function handleCancelImpersonate() {
    setImpersonateTarget(null);
    setImpersonateError(null);
  }
  function handleConfirmImpersonate() {
    if (impersonateTarget) {
      setImpersonateError(null);
      impersonateMutation.mutate(impersonateTarget);
    }
  }
  const impersonateTrapRef = useFocusTrap<HTMLDivElement>(
    !!impersonateTarget,
    handleCancelImpersonate,
  );

  const assignRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      bffPut<PlatformTenantUser>(
        `/admin/platform/tenants/${tenantId}/users/${userId}/role`,
        { role },
      ),
    onSuccess: () => {
      setAssignError(null);
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (err) => {
      // R4 — a failed reassignment is never silent (an earlier gap here was
      // caught by an independent adversarial review before this task closed).
      setAssignError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  const users = usersQuery.data?.users ?? [];
  const callerUserId = currentUser?.user_id ?? null;

  const columns = React.useMemo<ColumnDef<PlatformTenantUser>[]>(
    () => [
      {
        accessorKey: "email",
        header: "Email",
        cell: ({ row }) => <span className="text-foreground">{row.original.email}</span>,
      },
      {
        accessorKey: "role",
        header: "Current Role",
        cell: ({ row }) => (
          <span className="capitalize text-muted-foreground">{row.original.role}</span>
        ),
      },
      {
        id: "assign",
        header: "Assign Role",
        enableSorting: false,
        cell: ({ row }) => {
          const user = row.original;
          const isSelf = callerUserId ? user.id === callerUserId : false;
          if (isSelf) {
            return <span className="text-xs italic text-muted-foreground">(your account)</span>;
          }
          return (
            <select
              aria-label={`Assign role to ${user.email}`}
              defaultValue={user.role}
              disabled={assignRole.isPending}
              onChange={(e) => assignRole.mutate({ userId: user.id, role: e.target.value })}
              className="rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {ALL_ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          );
        },
      },
      {
        id: "impersonate",
        header: "Impersonate",
        enableSorting: false,
        cell: ({ row }) => {
          const user = row.original;
          const isSelf = callerUserId ? user.id === callerUserId : false;
          // Fails CLOSED: absent/unrecognized tenantKind (anything other than
          // exactly "customer") hides this action for every row — this is what
          // keeps tests/platform-members.test.tsx (which never passes tenantKind)
          // passing unmodified. Also hidden for the caller's own row and any
          // role==="superadmin" row (R3) — a superadmin never impersonates itself
          // or a peer superadmin.
          const eligible = tenantKind === "customer" && !isSelf && user.role !== "superadmin";
          if (!eligible) {
            return null;
          }
          const alreadyActive = impersonationStatus.data?.active === true;
          return (
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={alreadyActive}
                aria-label={`Impersonate ${user.email}`}
                onClick={() => setImpersonateTarget(user)}
              >
                Impersonate
              </Button>
              {alreadyActive && (
                // R4: a proactively-disabled control always carries an inline
                // explanation, never a silent disable.
                <span className="text-xs italic text-muted-foreground">
                  Already active — end the current session first
                </span>
              )}
            </div>
          );
        },
      },
    ],
    [callerUserId, assignRole, tenantKind, impersonationStatus.data?.active],
  );

  if (usersQuery.isLoading) {
    return <Loading label="Loading members" className="animate-pulse" />;
  }
  if (usersQuery.isError) {
    return (
      <ErrorState title={getErrorTitle(usersQuery.error)} onRetry={() => void usersQuery.refetch()} />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {assignError && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {assignError}
        </p>
      )}
      <DataTable columns={columns} data={users} ariaLabel="Members" emptyMessage="No members yet." />

      {impersonateTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="impersonate-overlay"
        >
          <div
            ref={impersonateTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm impersonation"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              Impersonate <span className="font-medium">{impersonateTarget.email}</span>? You will
              act as this user until you end the session.
            </p>
            {impersonateError && (
              <p role="alert" aria-live="polite" className="mt-2 text-sm text-destructive">
                {impersonateError}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="destructive"
                disabled={impersonateMutation.isPending}
                onClick={handleConfirmImpersonate}
              >
                Confirm
              </Button>
              <Button type="button" variant="outline" onClick={handleCancelImpersonate}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
