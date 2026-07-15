"use client";

/**
 * PendingInvites — the "pending invites" half of /app/members (member-invite-ui).
 *
 * Fetches GET /admin/invites and renders each PENDING invite (status filtered
 * client-side — the list endpoint's contract carries a `status` field per item, so
 * a revoked/accepted invite the server still returns is never shown here; it has
 * moved to the tenant's audit trail, not this actionable surface).
 *
 * Signature touch (Tin-approved 2026-07-15): a single live "expires in Nd" chip per
 * row, computed from expires_at at render time and re-ticked every 60s (ExpiryChip) —
 * one deliberate accent, not badge soup. Copy-link is NOT available here: the
 * plaintext token is returned ONCE, at creation (InviteMemberDialog), and this list
 * endpoint never carries it — so the only action a row offers is Revoke.
 *
 * Revoke (DELETE /admin/invites/{id}) invalidates PENDING_INVITES_QUERY_KEY and
 * announces the result via a role="status" banner — mirrors MembersPage's own
 * lastChange/undo banner idiom (no toast system exists in this app).
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, X } from "lucide-react";
import { bffGet, bffDelete, BffError } from "@/lib/bff-client";
import { Loading, ErrorState, Button, Badge } from "@/components/ui";
import { DataTable } from "@/components/ui/data-table";
import type { PendingInviteListItem, InvitesListResponse } from "./types";

export const PENDING_INVITES_QUERY_KEY = ["pending-invites"];

const MS_PER_DAY = 86_400_000;
/** Re-tick the expiry chips this often so "expires in Nd" stays live without a reload. */
const TICK_INTERVAL_MS = 60_000;

function daysRemaining(expiresAt: string): number {
  const ms = new Date(expiresAt).getTime() - Date.now();
  return Math.max(0, Math.ceil(ms / MS_PER_DAY));
}

function ExpiryChip({ expiresAt }: { expiresAt: string }) {
  const [, tick] = React.useReducer((c: number) => c + 1, 0);
  React.useEffect(() => {
    const id = setInterval(tick, TICK_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  const days = daysRemaining(expiresAt);
  const label = days <= 0 ? "Expires today" : `Expires in ${days}d`;
  return (
    <Badge variant={days <= 1 ? "warning" : "secondary"} aria-label={label}>
      {label}
    </Badge>
  );
}

function errorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load pending invites";
}

export function PendingInvites() {
  const queryClient = useQueryClient();
  const [revokedNotice, setRevokedNotice] = React.useState<string | null>(null);

  const invitesQuery = useQuery<InvitesListResponse>({
    queryKey: PENDING_INVITES_QUERY_KEY,
    queryFn: () => bffGet<InvitesListResponse>("/admin/invites"),
  });

  const revoke = useMutation({
    mutationFn: (inviteId: string) => bffDelete(`/admin/invites/${inviteId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: PENDING_INVITES_QUERY_KEY });
    },
  });

  const pending = (invitesQuery.data?.invites ?? []).filter((i) => i.status === "pending");

  const columns = React.useMemo<ColumnDef<PendingInviteListItem>[]>(
    () => [
      {
        accessorKey: "email",
        header: "Email",
        cell: ({ row }) => <span className="text-foreground">{row.original.email}</span>,
      },
      {
        accessorKey: "role",
        header: "Role",
        cell: ({ row }) => <span className="capitalize text-muted-foreground">{row.original.role}</span>,
      },
      {
        id: "expires",
        header: "Expires",
        enableSorting: false,
        cell: ({ row }) => <ExpiryChip expiresAt={row.original.expires_at} />,
      },
      {
        id: "revoke",
        header: () => <span className="sr-only">Actions</span>,
        enableSorting: false,
        cell: ({ row }) => {
          const invite = row.original;
          return (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={`Revoke invite to ${invite.email}`}
              disabled={revoke.isPending}
              onClick={() => {
                setRevokedNotice(null);
                revoke.mutate(invite.id, {
                  onSuccess: () => setRevokedNotice(`Revoked the invite to ${invite.email}.`),
                });
              }}
            >
              <X className="size-3.5" aria-hidden="true" />
              Revoke
            </Button>
          );
        },
      },
    ],
    [revoke],
  );

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-foreground">Pending invites</h2>

      {revokedNotice && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/5 px-4 py-3 text-sm text-success-text"
        >
          <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
          <span>{revokedNotice}</span>
        </div>
      )}

      {invitesQuery.isLoading ? (
        <Loading label="Loading pending invites…" />
      ) : invitesQuery.isError ? (
        <ErrorState title={errorTitle(invitesQuery.error)} />
      ) : (
        <DataTable
          columns={columns}
          data={pending}
          ariaLabel="Pending invites"
          emptyMessage="No pending invites."
        />
      )}
    </div>
  );
}
