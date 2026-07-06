"use client";

/**
 * PlatformActivityTab — Activity tab (6th tab, tenant-activity-tab TASK.md §3) of the
 * tenant-detail screen: lists the TARGET tenant's real audit-event history (actor/action/
 * target/result/when), newest-first, via the superadmin-scoped
 * GET /admin/platform/tenants/{tenantId}/audit route (platform_audit_router.py, FROZEN @ v1)
 * — a second, any-target-tenant door onto the same audit_events trail the self-service
 * AuditPage.tsx/AuditTable.tsx already exposes for a caller's own tenant.
 *
 * Structural precedent: PlatformMembersTab.tsx ({tenantId}-keyed own useQuery, local
 * getErrorTitle helper, Loading/ErrorState early-return before the main render) — with
 * PlatformTenantDirectory.tsx's own server-driven Previous/Next pagination chrome grafted on
 * top, because audit history for an active tenant is an unbounded, ever-growing collection
 * (the same condition that component's own docblock names as its reason for rejecting
 * DataTable's client-side-only `pageSizeOptions` pagination — R9, never used here either).
 *
 * Column set mirrors AuditTable.tsx's own COLUMNS exactly (Actor/Action/Target/Result/When,
 * When via the shared formatTimestamp) — a null actor_email/target_type renders "—" via each
 * column's own cell (M13), extending formatTimestamp's own null-placeholder convention.
 */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { bffGet, BffError } from "@/lib/bff-client";
import { DataTable, Loading, ErrorState, Empty } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";

export interface PlatformActivityTabProps {
  tenantId: string;
}

interface ActivityRow {
  id: string;
  actor_email: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  result: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface ActivityListResponse {
  items: ActivityRow[];
  total: number;
}

// Own constant, matching PlatformTenantDirectory.tsx's own PAGE_LIMIT value for visual
// consistency across the console — deliberately NOT the backend's own default limit=50
// (§1 Assumptions).
const ACTIVITY_PAGE_LIMIT = 20;

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

const COLUMNS: ColumnDef<ActivityRow>[] = [
  {
    accessorKey: "actor_email",
    header: "Actor",
    cell: ({ row }) => row.original.actor_email ?? "—",
  },
  { accessorKey: "action", header: "Action" },
  {
    accessorKey: "target_type",
    header: "Target",
    cell: ({ row }) => row.original.target_type ?? "—",
  },
  { accessorKey: "result", header: "Result" },
  {
    accessorKey: "created_at",
    header: "When",
    cell: ({ row }) => formatTimestamp(row.original.created_at),
  },
];

export function PlatformActivityTab({ tenantId }: PlatformActivityTabProps) {
  // A tenantId change unmounts/remounts this component under TabsContent (matching every
  // other tab's own precedent), so no manual reset effect is needed here.
  const [offset, setOffset] = React.useState(0);

  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ActivityListResponse>({
    queryKey: ["platform-tenant-audit", tenantId, offset],
    queryFn: () =>
      bffGet<ActivityListResponse>(
        `/admin/platform/tenants/${tenantId}/audit?limit=${ACTIVITY_PAGE_LIMIT}&offset=${offset}`,
      ),
    retry: false,
  });

  if (isLoading) {
    return <Loading label="Loading activity" className="animate-pulse" />;
  }

  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;

  if (total === 0) {
    return <Empty title="No audit events yet" />;
  }

  return (
    <div className="flex flex-col gap-3">
      <DataTable
        columns={COLUMNS}
        data={items}
        ariaLabel="Activity"
        emptyMessage="No audit events yet"
      />

      <div className="flex flex-wrap items-center justify-between gap-3 px-1 text-sm">
        <span aria-live="polite" className="text-muted-foreground">
          Showing {offset + 1}–{offset + items.length} of {total}
        </span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - ACTIVITY_PAGE_LIMIT))}
            disabled={offset === 0}
            className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + ACTIVITY_PAGE_LIMIT)}
            disabled={offset + items.length >= total}
            className="inline-flex items-center rounded-md border border-border bg-card px-2 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
