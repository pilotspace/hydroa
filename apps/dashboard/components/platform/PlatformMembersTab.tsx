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
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { DataTable } from "@/components/ui/data-table";
import { useCurrentUser } from "@/lib/hooks/use-current-user";

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

export interface PlatformMembersTabProps {
  tenantId: string;
}

export function PlatformMembersTab({ tenantId }: PlatformMembersTabProps) {
  const queryClient = useQueryClient();
  const { data: currentUser } = useCurrentUser();
  const queryKey = React.useMemo(() => ["platform-tenant-users", tenantId], [tenantId]);

  const usersQuery = useQuery<UsersListResponse>({
    queryKey,
    queryFn: () => bffGet<UsersListResponse>(`/admin/platform/tenants/${tenantId}/users`),
    retry: false,
  });

  const [assignError, setAssignError] = React.useState<string | null>(null);

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
    ],
    [callerUserId, assignRole],
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
    </div>
  );
}
