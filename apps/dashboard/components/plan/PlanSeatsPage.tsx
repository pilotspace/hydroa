"use client";

/**
 * PlanSeatsPage — the tenant-self Plan & seats console (billing-ui TASK.md §3 CONTRACT
 * M7/M8, R6, R7). Composes THREE reads: the new GET /admin/plan (this task's own endpoint),
 * GET /admin/budget (spent_usd_month, reused verbatim), and GET /admin/users (roster length,
 * reused — the already-shipped members list; degrades gracefully if it errors, since a seat
 * COUNT read failure should never block the rest of the page).
 *
 * An unplanned tenant (`plan: null`) is NOT an error (R6) — "No plan assigned" renders while
 * the tenant-level resolved budget still meters. RPM/TPM render as static catalog reference
 * values, never a live meter. "Per-seat pricing" is an inert placeholder (R7) — seat-billing
 * (wave-2 sibling) has not shipped its own pricing endpoint yet; no fetch is ever attempted
 * against it.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import { Loading, ErrorState, Badge, Card, CardHeader, CardTitle, CardContent } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { formatNumber } from "@/lib/format";
import { EntitlementMeter } from "./EntitlementMeter";

interface PlanSummary {
  id: string;
  name: string;
  display_name: string;
  seat_cap: number | null;
  rpm_limit_default: number | null;
  tpm_limit_default: number | null;
}

interface PlanGetResponse {
  plan: PlanSummary | null;
  resolved: {
    effective_budget_usd_monthly: string | null;
    plan_model_allowlist: string[] | null;
    plan_feature_flags: string[];
  };
}

interface BudgetGetResponse {
  budget_usd_monthly: string | null;
  spent_usd_month: string;
}

interface UsersListResponse {
  users: Array<{ id: string; email: string; role: string }>;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load your plan";
}

export function PlanSeatsPage() {
  const planQuery = useQuery<PlanGetResponse>({
    queryKey: ["admin-plan"],
    queryFn: () => bffGet<PlanGetResponse>("/admin/plan"),
    retry: false,
  });

  const budgetQuery = useQuery<BudgetGetResponse>({
    queryKey: ["admin-budget"],
    queryFn: () => bffGet<BudgetGetResponse>("/admin/budget"),
    retry: false,
  });

  // Roster count degrades gracefully — a failure here never blocks the rest of the page
  // (dispatch instruction; the seat count line just falls back to "—").
  const usersQuery = useQuery<UsersListResponse>({
    queryKey: ["admin-users"],
    queryFn: () => bffGet<UsersListResponse>("/admin/users"),
    retry: false,
  });

  const isLoading = planQuery.isLoading || budgetQuery.isLoading;
  const isError = planQuery.isError || budgetQuery.isError;

  const plan = planQuery.data?.plan ?? null;
  const resolved = planQuery.data?.resolved;
  const spentUsd = budgetQuery.data?.spent_usd_month ?? "0.00";
  const memberCount = usersQuery.data?.users.length;

  return (
    <section aria-labelledby="plan-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Plan & seats"
        titleId="plan-heading"
        description="Your tenant's assigned plan and the limits it enforces."
      />

      {isLoading ? (
        <Loading label="Loading your plan…" />
      ) : isError ? (
        <ErrorState title={getErrorTitle(planQuery.error ?? budgetQuery.error)} />
      ) : resolved ? (
        <Card>
          <CardHeader>
            <CardTitle>
              {plan ? plan.display_name : "No plan assigned — usage governed by tenant-level defaults only"}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <EntitlementMeter
              label="Budget"
              valueUsd={spentUsd}
              ceilingUsd={resolved.effective_budget_usd_monthly}
            />

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Models</span>
                <span className="text-foreground">
                  {resolved.plan_model_allowlist === null ? "All models" : resolved.plan_model_allowlist.join(", ")}
                </span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Features</span>
                {resolved.plan_feature_flags.length === 0 ? (
                  <span className="text-foreground">None</span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {resolved.plan_feature_flags.map((flag) => (
                      <Badge key={flag} variant="outline">
                        {flag}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {plan ? (
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Seats</span>
                  <span className="font-mono tabular-nums text-foreground">
                    {memberCount ?? "—"} of {plan.seat_cap ?? "Unlimited"} seats
                  </span>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Requests/min (plan reference)
                  </span>
                  <span className="font-mono tabular-nums text-foreground">
                    {plan.rpm_limit_default === null ? "Unlimited" : formatNumber(plan.rpm_limit_default)}
                  </span>
                </div>
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Tokens/min (plan reference)
                  </span>
                  <span className="font-mono tabular-nums text-foreground">
                    {plan.tpm_limit_default === null ? "Unlimited" : formatNumber(plan.tpm_limit_default)}
                  </span>
                </div>
              </div>
            ) : null}

            {plan ? (
              <div className="rounded-md border border-dashed border-border p-3 text-sm text-muted-foreground">
                Seat pricing coming soon.
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}
