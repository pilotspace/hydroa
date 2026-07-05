"use client";

/**
 * PlatformPlanTab — Screen 2's 5th tab ("Plan") on the tenant-detail shell: a
 * superadmin can view, assign, switch, adjust, or remove ONE tenant's plan
 * assignment, with all 3 catalog tiers shown side-by-side for direct ceiling
 * comparison (TASK.md M3-M11).
 *
 * 3 independent GETs, a UNIFIED loading/error gate (TASK.md §3 CONTRACT — any
 * one of the 3 failing renders a full ErrorState, no partial/stale grid, R5):
 *   (a) GET .../tenants/{tenantId}        -> {..., kind} — SHARED cache key
 *       (["platform-tenant", tenantId]) with PlatformTenantDetail's own
 *       identical query; read ONLY for `.kind` (M8's pre-empt check).
 *   (b) GET .../tenants/{tenantId}/plan    -> TenantPlanResponse
 *       {tenant_id, plan: PlanResponse|null, seat_cap}
 *   (c) GET .../plans                      -> PlansListResponse — SHARED
 *       cache key (["platform-plans"]) with PlatformPlanCatalog (Screen 1).
 *
 * Safety rule (TASK.md §5): the PUT body's `seat_cap` key is a PRESENCE check
 * — a `touched` flag set on ANY edit of the pre-filled field — never a
 * value-equality check against the prefilled default. Mirrors the backend's
 * own `body.model_fields_set` presence semantics (platform_plans_router.py,
 * read directly this session). Getting this wrong would silently reintroduce
 * an override-vs-inherit bug: re-entering the SAME number that was already
 * pre-filled must still be sent EXPLICITLY, not misread as "unchanged."
 */

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Button, Badge, Input, Loading, ErrorState } from "@/components/ui";
import { useFocusTrap } from "@/lib/use-focus-trap";
import {
  PlanCard,
  PlanCardGrid,
  type PlanResponse,
  type PlansListResponse,
} from "./PlatformPlanCatalog";

interface TenantKindSummary {
  id: string;
  name: string;
  kind: string;
  created_at: string;
}

interface TenantPlanResponse {
  tenant_id: string;
  plan: PlanResponse | null;
  seat_cap: number | null;
}

/** `seat_cap` is OPTIONAL (3-state: omitted/null/positive-int) — see file
 * header Safety rule. Omitted means "inherit the plan's own default"; an
 * explicit `null` means "unlimited override." */
type PutPlanBody = { plan_id: string | null; seat_cap?: number | null };

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

type SeatCapValidation = { value: number | null } | { error: string };

/** blank -> null (explicit unlimited override); else must be a positive
 * whole number — zero/negative/non-numeric rejected client-side before any
 * request fires (M9/R4). */
function validateSeatCap(raw: string): SeatCapValidation {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null };
  if (!/^\d+$/.test(trimmed) || Number(trimmed) <= 0) {
    return { error: "Enter a positive whole number, or leave empty for unlimited" };
  }
  return { value: Number(trimmed) };
}

export interface PlatformPlanTabProps {
  tenantId: string;
}

export function PlatformPlanTab({ tenantId }: PlatformPlanTabProps) {
  const queryClient = useQueryClient();
  const planQueryKey = React.useMemo(() => ["platform-tenant-plan", tenantId], [tenantId]);

  const tenantQuery = useQuery<TenantKindSummary>({
    queryKey: ["platform-tenant", tenantId],
    queryFn: () => bffGet<TenantKindSummary>(`/admin/platform/tenants/${tenantId}`),
    retry: false,
  });
  const planQuery = useQuery<TenantPlanResponse>({
    queryKey: planQueryKey,
    queryFn: () => bffGet<TenantPlanResponse>(`/admin/platform/tenants/${tenantId}/plan`),
    retry: false,
  });
  const plansQuery = useQuery<PlansListResponse>({
    queryKey: ["platform-plans"],
    queryFn: () => bffGet<PlansListResponse>("/admin/platform/plans"),
    retry: false,
  });

  // ── inline assign / switch / adjust confirm (reveal-below-grid, never a
  // modal — mirrors PlatformBudgetTab's own `isEditing` reveal shape) ──
  const [targetPlan, setTargetPlan] = React.useState<PlanResponse | null>(null);
  const [seatCapDraft, setSeatCapDraft] = React.useState("");
  const [seatCapTouched, setSeatCapTouched] = React.useState(false);
  const [fieldError, setFieldError] = React.useState<string | null>(null);
  const [confirmServerError, setConfirmServerError] = React.useState<string | null>(null);

  // ── remove-plan-assignment focus-trapped dialog (mirrors PlatformKeysTab's
  // revoke-confirm verbatim) ──
  const [removeDialogOpen, setRemoveDialogOpen] = React.useState(false);
  const [removeError, setRemoveError] = React.useState<string | null>(null);

  const assignMutation = useMutation({
    mutationFn: (body: PutPlanBody) =>
      bffPut<TenantPlanResponse>(`/admin/platform/tenants/${tenantId}/plan`, body),
    onSuccess: (resp) => {
      queryClient.setQueryData(planQueryKey, resp);
      setTargetPlan(null);
      setFieldError(null);
      setConfirmServerError(null);
    },
    onError: (err) => {
      // Kept OPEN on failure (mirrors PlatformKeysTab's revoke-kept-open-on-
      // error precedent) so the superadmin sees the error and can retry or
      // cancel explicitly — the failed attempt is never optimistically
      // applied (R2, R3).
      setConfirmServerError(getErrorTitle(err));
    },
  });

  const removeMutation = useMutation({
    mutationFn: () =>
      bffPut<TenantPlanResponse>(`/admin/platform/tenants/${tenantId}/plan`, {
        plan_id: null,
      } satisfies PutPlanBody),
    onSuccess: (resp) => {
      queryClient.setQueryData(planQueryKey, resp);
      setRemoveDialogOpen(false);
      setRemoveError(null);
    },
    onError: (err) => {
      setRemoveError(getErrorTitle(err));
    },
  });

  function handleCancelRemove() {
    setRemoveDialogOpen(false);
    setRemoveError(null);
  }
  function handleConfirmRemove() {
    setRemoveError(null);
    removeMutation.mutate();
  }
  const removeTrapRef = useFocusTrap<HTMLDivElement>(removeDialogOpen, handleCancelRemove);

  // Loading/data guards happen before any hook-order-sensitive logic below —
  // all hooks above are unconditional (rules-of-hooks safe).
  const isLoading = tenantQuery.isLoading || planQuery.isLoading || plansQuery.isLoading;
  const isError = tenantQuery.isError || planQuery.isError || plansQuery.isError;
  const firstError = tenantQuery.error ?? planQuery.error ?? plansQuery.error;

  function retryAll() {
    if (tenantQuery.isError) void tenantQuery.refetch();
    if (planQuery.isError) void planQuery.refetch();
    if (plansQuery.isError) void plansQuery.refetch();
  }

  if (isLoading) {
    return <Loading label="Loading plan" className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(firstError)} onRetry={retryAll} />;
  }
  if (!tenantQuery.data || !planQuery.data || !plansQuery.data) {
    return null;
  }

  const tenant = tenantQuery.data;
  const tenantPlan = planQuery.data;
  const catalog = plansQuery.data.plans;

  // M8: the platform's own reserved tenant is pre-emptively read-only —
  // nothing that could ever fire a PUT is rendered, client-side, before any
  // 403 could be reached.
  if (tenant.kind === "platform") {
    return (
      <p className="text-sm text-muted-foreground">
        The platform tenant is Hydroa&apos;s own reserved tenant and is not eligible for plan
        assignment.
      </p>
    );
  }

  const currentPlanId = tenantPlan.plan?.id ?? null;

  function openConfirm(plan: PlanResponse) {
    const prefill = plan.id === currentPlanId ? tenantPlan.seat_cap : plan.seat_cap;
    setTargetPlan(plan);
    setSeatCapDraft(prefill === null ? "" : String(prefill));
    setSeatCapTouched(false);
    setFieldError(null);
    setConfirmServerError(null);
  }

  function closeConfirm() {
    setTargetPlan(null);
    setFieldError(null);
    setConfirmServerError(null);
  }

  function handleSeatCapChange(e: React.ChangeEvent<HTMLInputElement>) {
    setSeatCapDraft(e.target.value);
    setSeatCapTouched(true);
  }

  function handleSaveConfirm() {
    if (!targetPlan) return;
    setFieldError(null);
    setConfirmServerError(null);
    if (!seatCapTouched) {
      if (targetPlan.id === currentPlanId) {
        // Adjusting the CURRENT plan without editing the pre-filled value: the
        // tenant's own custom seat_cap is already known (it's what was pre-filled,
        // see openConfirm) — send it EXPLICITLY so an un-edited Save preserves it,
        // rather than omitting and falling through to the plan's generic default
        // (review finding: omitting here silently reset a negotiated custom cap).
        assignMutation.mutate({ plan_id: targetPlan.id, seat_cap: tenantPlan.seat_cap });
        return;
      }
      // Assigning/switching to a DIFFERENT plan, untouched: inherit that plan's
      // own default — seat_cap key OMITTED (M5, plan-catalog's own M8).
      assignMutation.mutate({ plan_id: targetPlan.id });
      return;
    }
    const result = validateSeatCap(seatCapDraft);
    if ("error" in result) {
      setFieldError(result.error);
      return;
    }
    // Touched: EXPLICIT override, including a blanked field as literal null
    // (M5, plan-catalog's own M9).
    assignMutation.mutate({ plan_id: targetPlan.id, seat_cap: result.value });
  }

  return (
    <div className="flex flex-col gap-6">
      {tenantPlan.plan === null && (
        <p className="text-sm text-muted-foreground">No plan assigned.</p>
      )}

      <PlanCardGrid>
        {catalog.map((plan) => {
          const isCurrent = plan.id === currentPlanId;
          return (
            <PlanCard
              key={plan.id}
              plan={plan}
              displaySeatCap={isCurrent ? tenantPlan.seat_cap : undefined}
              badge={isCurrent ? <Badge variant="secondary">Current plan</Badge> : undefined}
              action={
                isCurrent ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => openConfirm(plan)}
                  >
                    Adjust seat cap
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => openConfirm(plan)}
                  >
                    Assign {plan.display_name}
                  </Button>
                )
              }
            />
          );
        })}
      </PlanCardGrid>

      {targetPlan && (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
          <div className="flex flex-col gap-1">
            <label
              htmlFor="platform-plan-seatcap-input"
              className="text-sm font-medium text-foreground"
            >
              Seats
            </label>
            <Input
              id="platform-plan-seatcap-input"
              type="text"
              inputMode="numeric"
              value={seatCapDraft}
              onChange={handleSeatCapChange}
              placeholder="leave empty for unlimited"
              aria-label="Seats"
            />
            {fieldError && (
              <p role="alert" aria-live="assertive" className="text-sm text-destructive">
                {fieldError}
              </p>
            )}
            {confirmServerError && (
              <p role="alert" aria-live="assertive" className="text-sm text-destructive">
                {confirmServerError}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button type="button" disabled={assignMutation.isPending} onClick={handleSaveConfirm}>
              {assignMutation.isPending ? "Saving…" : "Save"}
            </Button>
            <Button type="button" variant="outline" onClick={closeConfirm}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {tenantPlan.plan !== null && (
        <div>
          <Button type="button" variant="ghost" onClick={() => setRemoveDialogOpen(true)}>
            Remove plan assignment
          </Button>
        </div>
      )}

      {removeDialogOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          data-testid="platform-plan-remove-overlay"
        >
          <div
            ref={removeTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label="Confirm plan removal"
            className="w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-lg"
          >
            <p className="text-sm text-foreground">
              Remove this tenant&apos;s plan assignment? Its seat cap is cleared too.
            </p>
            {removeError && (
              <p role="alert" aria-live="polite" className="mt-2 text-sm text-destructive">
                {removeError}
              </p>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <Button
                type="button"
                variant="destructive"
                disabled={removeMutation.isPending}
                onClick={handleConfirmRemove}
              >
                Confirm
              </Button>
              <Button type="button" variant="outline" onClick={handleCancelRemove}>
                Cancel
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
