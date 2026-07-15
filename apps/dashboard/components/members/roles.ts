/**
 * components/members/roles.ts — the tenant role-escalation policy shared by
 * MembersPage (role reassignment) and InviteMemberDialog (invite role select).
 *
 * Lifted out of MembersPage.tsx (member-invite-ui) so the invite flow reuses the
 * EXACT same convenience filter instead of duplicating it — a second, drifting
 * copy would be the failure mode this module exists to prevent. UI-convenience
 * filtering only: the gateway independently enforces these limits server-side.
 *
 * superadmin is a PLATFORM-level role (see components/platform/*) — it never
 * appears in this tenant-level tier list, so it is never invitable/assignable here.
 */

export const ALL_ROLES = [
  "owner",
  "admin",
  "operator",
  "billing_admin",
  "viewer",
  "member",
] as const;

export type RoleTier = (typeof ALL_ROLES)[number];

/** Roles the caller is allowed to assign/invite (convenience filtering; server enforces). */
export function assignableRoles(callerRole: string): RoleTier[] {
  if (callerRole === "owner") return [...ALL_ROLES];
  if (callerRole === "admin") return ["operator", "billing_admin", "viewer", "member"];
  return []; // lower roles have no assignment/invite ability (403 at the server)
}
