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

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPut, ApiError } from "@/lib/api-client";
import { Loading, ErrorState } from "@/components/ui";

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
  const assignable = assignableRoles(callerRole);

  const usersQuery = useQuery<UsersListResponse>({
    queryKey: ["admin-users"],
    queryFn: () => apiGet<UsersListResponse>("/admin/users"),
  });

  const assignRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      apiPut<TenantUser>(`/admin/users/${userId}/role`, { role }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });

  const users = usersQuery.data?.users ?? [];

  return (
    <section aria-labelledby="members-heading" className="flex flex-col gap-6">
      <h1
        id="members-heading"
        className="text-2xl font-semibold tracking-tight text-foreground"
      >
        Members
      </h1>

      {usersQuery.isLoading ? (
        <Loading label="Loading members…" />
      ) : usersQuery.isError ? (
        <ErrorState
          title={
            usersQuery.error instanceof ApiError
              ? usersQuery.error.problem.title
              : usersQuery.error instanceof Error
              ? usersQuery.error.message
              : "Failed to load members"
          }
        />
      ) : users.length === 0 ? (
        <p className="text-sm text-muted-foreground">No members yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/50">
              <tr>
                <th
                  scope="col"
                  className="px-4 py-3 text-left font-medium text-muted-foreground"
                >
                  Email
                </th>
                <th
                  scope="col"
                  className="px-4 py-3 text-left font-medium text-muted-foreground"
                >
                  Current Role
                </th>
                <th
                  scope="col"
                  className="px-4 py-3 text-left font-medium text-muted-foreground"
                >
                  Assign Role
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.map((user) => {
                const isSelf = callerUserId ? user.id === callerUserId : false;
                return (
                  <tr key={user.id} className="bg-card hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3 text-foreground">{user.email}</td>
                    <td className="px-4 py-3 text-muted-foreground capitalize">
                      {user.role}
                    </td>
                    <td className="px-4 py-3">
                      {isSelf ? (
                        <span className="text-xs text-muted-foreground italic">
                          (your account)
                        </span>
                      ) : (
                        <select
                          aria-label={`Assign role to ${user.email}`}
                          defaultValue={user.role}
                          disabled={assignable.length === 0 || assignRole.isPending}
                          onChange={(e) => {
                            assignRole.mutate({
                              userId: user.id,
                              role: e.target.value,
                            });
                          }}
                          className="rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                        >
                          {assignable.map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
