"use client";

/**
 * MembersPage — tenant user role-assignment surface (/app/members).
 *
 * Fetches GET /admin/users (via the BFF catch-all /api/gw/admin/users) and
 * renders the list of tenant users with a per-user role selector.
 *
 * ESCALATION POLICY (convenience filtering — server-side is authoritative):
 *   owner sees all 6 role options (owner/admin/operator/billing_admin/viewer/member).
 *   admin sees only {operator/billing_admin/viewer/member} (NOT owner/admin).
 *   The server enforces these limits independently; UI filtering is UX-only.
 *
 * SELF-GUARD: the role selector is disabled for the row matching the caller's
 * own user_id so the UI cannot even attempt a self-role-change.
 *
 * Auth guard: proxy.ts handles cookie presence server-side.
 * The gateway's MEMBERS_MANAGE enforcement keeps lower roles out.
 *
 * Accessibility: one <h1>, table with column headers, per-user aria-label on
 * the role selector, WCAG-AA compliant.
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/page-header";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TenantUser {
  id: string;
  email: string;
  role: string;
}

interface UsersListResponse {
  users: TenantUser[];
}

// ---------------------------------------------------------------------------
// Escalation policy — all 6 tiers
// ---------------------------------------------------------------------------

const ALL_ROLES = ["owner", "admin", "operator", "billing_admin", "viewer", "member"] as const;
type RoleTier = (typeof ALL_ROLES)[number];

/** Roles the caller is allowed to assign (convenience filtering; server enforces). */
function assignableRoles(callerRole: string): RoleTier[] {
  if (callerRole === "owner") return [...ALL_ROLES];
  if (callerRole === "admin")
    return ["operator", "billing_admin", "viewer", "member"];
  return []; // lower roles have no assignment ability (403 at the server)
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MembersPageProps {
  /** The authenticated caller's role — used to filter role options (UI convenience). */
  callerRole: string;
  /** The authenticated caller's user_id — used to disable the self-change selector. */
  callerUserId?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MembersPage({ callerRole, callerUserId }: MembersPageProps) {
  const queryClient = useQueryClient();
  const assignable = React.useMemo(() => assignableRoles(callerRole), [callerRole]);

  const usersQuery = useQuery<UsersListResponse>({
    queryKey: ["admin-users"],
    queryFn: () => bffGet<UsersListResponse>("/admin/users"),
  });

  const assignRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      bffPut<TenantUser>(`/admin/users/${userId}/role`, { role }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const users = usersQuery.data?.users ?? [];

  const columns = React.useMemo<ColumnDef<TenantUser>[]>(
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
              disabled={assignable.length === 0 || assignRole.isPending}
              onChange={(e) => assignRole.mutate({ userId: user.id, role: e.target.value })}
              className="rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              {assignable.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          );
        },
      },
    ],
    [assignable, callerUserId, assignRole],
  );

  return (
    <section aria-labelledby="members-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Members"
        titleId="members-heading"
        description="Manage tenant members and their roles."
      />

      {usersQuery.isLoading ? (
        <Loading label="Loading members…" />
      ) : usersQuery.isError ? (
        <ErrorState
          title={
            usersQuery.error instanceof BffError
              ? usersQuery.error.problem.title
              : usersQuery.error instanceof Error
              ? usersQuery.error.message
              : "Failed to load members"
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={users}
          ariaLabel="Members"
          emptyMessage="No members yet."
        />
      )}
    </section>
  );
}
