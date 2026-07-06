"use client";

/**
 * PlatformTenantOverviewStrip — tenant-overview-strip TASK.md (M1-M8): a
 * small, always-visible summary region at the top of the tenant-detail page
 * (mounts in PlatformTenantDetail.tsx as a sibling between <PageHeader/> and
 * <Tabs>, inside the existing `flex flex-col gap-6` shell — never inside a
 * TabsContent), showing plan+seat-cap, seats-used, active-keys, and budget.
 *
 * 4 independent useQuery calls, each BYTE-IDENTICAL (key array + URL) to its
 * sibling tab's own — the cache-key-reuse risk this task exists to manage
 * (MILESTONE.md's own "Shared/risky contracts" line): an already-cached tab
 * fetch is shared via React Query's own cache, never re-fetched, and one
 * tile's own loading/error state never blanks, blocks, or delays the other 3.
 *   Plan+seat-cap : mirrors PlatformPlanTab's own planQueryKey
 *                   (["platform-tenant-plan", tenantId] -> GET .../plan)
 *   Seats-used    : mirrors PlatformMembersTab's own queryKey
 *                   (["platform-tenant-users", tenantId] -> GET .../users)
 *   Active keys   : mirrors PlatformKeysTab's own queryKey
 *                   (["platform-tenant-keys", tenantId] -> GET .../keys)
 *   Budget        : mirrors PlatformBudgetTab's own queryKey — a plain array
 *                   literal there (NOT React.useMemo'd, unlike its 3
 *                   siblings) -> GET .../budget. Matched here as a literal
 *                   too (TASK.md §0 Issues/Risks #4) — React Query compares
 *                   keys by serialized value, so this is harmless for cache
 *                   correctness; "fixing" that file's own pre-existing
 *                   inconsistency is out of this task's declared Scope.
 *
 * All 4 response types (TenantPlanResponse/UsersListResponse/PlatformApiKey/
 * BudgetData) are IMPORTED from their owning tab file (each gained an
 * additive `export` keyword — TASK.md M4) rather than re-declared locally, so
 * there is exactly one shape per contract, never a silently-drifting copy.
 *
 * Each tile is a pure-props sub-component (OverviewTile, exported below) fed
 * entirely by its parent query's isLoading/isError/data/refetch — CONVENTIONS.md's
 * v13 folded testability lesson ("pure-props components are deterministically
 * state-testable without msw") — rather than 4 independently-hook-calling
 * grandchildren.
 */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Card, StatCard, Loading, ErrorState } from "@/components/ui";
import type { TenantPlanResponse } from "./PlatformPlanTab";
import type { UsersListResponse } from "./PlatformMembersTab";
import type { PlatformApiKey } from "./PlatformKeysTab";
import type { BudgetData } from "./PlatformBudgetTab";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export interface OverviewTileProps {
  label: string;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  value?: React.ReactNode;
  footer?: React.ReactNode;
  /** Optional data-testid on the value node — mirrors StatCard's own
   * valueTestId passthrough, lets a caller keep a stable test hook. */
  valueTestId?: string;
}

/**
 * Pure-props tile (M5, CONVENTIONS.md v13 folded lesson): isLoading -> the
 * existing Loading primitive; isError -> the existing ErrorState, its own
 * onRetry bound to only THIS tile's query; otherwise -> StatCard
 * variant="flat". No new display primitive anywhere.
 */
export function OverviewTile({
  label,
  isLoading,
  isError,
  error,
  onRetry,
  value,
  footer,
  valueTestId,
}: OverviewTileProps) {
  if (isLoading) {
    return <Loading label={`Loading ${label.toLowerCase()}`} className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={onRetry} />;
  }
  return (
    <StatCard variant="flat" label={label} value={value} footer={footer} valueTestId={valueTestId} />
  );
}

export interface PlatformTenantOverviewStripProps {
  tenantId: string;
}

export function PlatformTenantOverviewStrip({ tenantId }: PlatformTenantOverviewStripProps) {
  // Plan+seat-cap — byte-identical to PlatformPlanTab's own planQueryKey (M3).
  const planQueryKey = React.useMemo(() => ["platform-tenant-plan", tenantId], [tenantId]);
  const planQuery = useQuery<TenantPlanResponse>({
    queryKey: planQueryKey,
    queryFn: () => bffGet<TenantPlanResponse>(`/admin/platform/tenants/${tenantId}/plan`),
    retry: false,
  });

  // Seats-used — byte-identical to PlatformMembersTab's own queryKey (M3).
  const usersQueryKey = React.useMemo(() => ["platform-tenant-users", tenantId], [tenantId]);
  const usersQuery = useQuery<UsersListResponse>({
    queryKey: usersQueryKey,
    queryFn: () => bffGet<UsersListResponse>(`/admin/platform/tenants/${tenantId}/users`),
    retry: false,
  });

  // Active keys — byte-identical to PlatformKeysTab's own queryKey (M3).
  const keysQueryKey = React.useMemo(() => ["platform-tenant-keys", tenantId], [tenantId]);
  const keysQuery = useQuery<PlatformApiKey[]>({
    queryKey: keysQueryKey,
    queryFn: () => bffGet<PlatformApiKey[]>(`/admin/platform/tenants/${tenantId}/keys`),
    retry: false,
  });

  // Budget — byte-identical to PlatformBudgetTab's own queryKey: a plain array
  // literal there, matched here as a literal too (see file header).
  const budgetQueryKey = ["platform-tenant-budget", tenantId];
  const budgetQuery = useQuery<BudgetData>({
    queryKey: budgetQueryKey,
    queryFn: () => bffGet<BudgetData>(`/admin/platform/tenants/${tenantId}/budget`),
    retry: false,
  });

  const users = usersQuery.data?.users ?? [];
  const activeKeysCount = (keysQuery.data ?? []).filter((k) => k.revoked_at === null).length;
  const seatCap = planQuery.data?.seat_cap;
  const budgetCeiling = budgetQuery.data?.budget_usd_monthly ?? null;

  return (
    <Card
      variant="flat"
      role="region"
      aria-label="Tenant overview"
      className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-4"
    >
      <OverviewTile
        label="Plan"
        isLoading={planQuery.isLoading}
        isError={planQuery.isError}
        error={planQuery.error}
        onRetry={() => void planQuery.refetch()}
        value={planQuery.data?.plan?.display_name ?? "No plan"}
        footer={seatCap != null ? `Seat cap: ${seatCap}` : "No seat cap set"}
        valueTestId="platform-overview-plan-value"
      />
      <OverviewTile
        label="Seats used"
        isLoading={usersQuery.isLoading}
        isError={usersQuery.isError}
        error={usersQuery.error}
        onRetry={() => void usersQuery.refetch()}
        value={users.length}
        valueTestId="platform-overview-users-value"
      />
      <OverviewTile
        label="Active keys"
        isLoading={keysQuery.isLoading}
        isError={keysQuery.isError}
        error={keysQuery.error}
        onRetry={() => void keysQuery.refetch()}
        value={activeKeysCount}
        valueTestId="platform-overview-keys-value"
      />
      <OverviewTile
        label="Budget"
        isLoading={budgetQuery.isLoading}
        isError={budgetQuery.isError}
        error={budgetQuery.error}
        onRetry={() => void budgetQuery.refetch()}
        value={budgetQuery.data?.spent_usd_month}
        footer={budgetCeiling != null ? `Cap: ${budgetCeiling}` : "No cap set"}
        valueTestId="platform-overview-budget-value"
      />
    </Card>
  );
}
