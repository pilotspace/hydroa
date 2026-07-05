"use client";

/**
 * PlatformPlanCatalog — Screen 1 of the plan-admin-ui task: a superadmin-only,
 * read-only 3-up (responsive) card grid of the plan/tier catalog
 * (/app/platform/plans — TASK.md M1).
 *
 * GET /admin/platform/plans -> PlansListResponse { plans: PlanResponse[] }
 * (CITED verbatim, plan-catalog §3 CONTRACT — the DTO shape is owned by the
 * already-shipped, frozen `platform_plans_router.py`, confirmed by reading it
 * directly this session, not redefined here). Every ceiling is rendered
 * human-labeled; a null default renders "Unlimited", mirroring
 * PlatformBudgetTab.tsx's own shipped null-ceiling convention exactly.
 *
 * `PlanCard`/`PlanCardGrid` are exported and reused VERBATIM by
 * `PlatformPlanTab.tsx` (Screen 2's per-tenant comparison grid) — TASK.md §3
 * CONTRACT describes Screen 2's own cards as "same 4 labeled rows as Screen
 * 1's cards, PLUS..." — reuse-over-invent (ui-designer persona), not a second
 * hand-rolled card.
 */

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet, BffError } from "@/lib/bff-client";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Loading,
  Empty,
  ErrorState,
} from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";

export interface PlanResponse {
  id: string;
  name: string;
  display_name: string;
  seat_cap: number | null;
  budget_usd_monthly_default: string | null;
  rpm_limit_default: number | null;
  tpm_limit_default: number | null;
}

export interface PlansListResponse {
  plans: PlanResponse[];
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

/** Null -> "Unlimited"; every other value renders verbatim (mirrors
 * PlatformBudgetTab.tsx's own shipped null-ceiling line exactly). */
function ceiling(value: string | number | null): string {
  return value === null ? "Unlimited" : String(value);
}

export interface PlanCardProps {
  plan: PlanResponse;
  /**
   * Overrides the "Seats" row's displayed value — e.g. a tenant's OWN
   * resolved seat_cap on its CURRENT tier's card, which may differ from the
   * catalog tier's own default once adjusted. Omitted (undefined) defaults to
   * the catalog's own `plan.seat_cap`; an explicit `null` renders "Unlimited".
   */
  displaySeatCap?: number | null;
  badge?: ReactNode;
  action?: ReactNode;
}

/** One tier's ceilings, human-labeled — shared by Screen 1 (catalog) and
 * Screen 2 (the per-tenant Plan tab's own comparison grid). */
export function PlanCard({ plan, displaySeatCap, badge, action }: PlanCardProps) {
  const seatCapValue = displaySeatCap === undefined ? plan.seat_cap : displaySeatCap;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{plan.display_name}</CardTitle>
        {badge}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <dl className="flex flex-col gap-1.5 text-sm">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Seats</dt>
            <dd className="font-medium text-foreground">{ceiling(seatCapValue)}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Monthly budget</dt>
            <dd className="font-medium text-foreground">
              {ceiling(plan.budget_usd_monthly_default)}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Requests/min</dt>
            <dd className="font-medium text-foreground">{ceiling(plan.rpm_limit_default)}</dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Tokens/min</dt>
            <dd className="font-medium text-foreground">{ceiling(plan.tpm_limit_default)}</dd>
          </div>
        </dl>
        {action}
      </CardContent>
    </Card>
  );
}

/** The shared 3-up (responsive) grid shell both screens render their
 * PlanCards into — one definition, so the layout can never drift between
 * the catalog page and the per-tenant tab. */
export function PlanCardGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 md:grid-cols-3">{children}</div>;
}

export function PlatformPlanCatalog() {
  const { data, isLoading, isError, error, refetch } = useQuery<PlansListResponse>({
    queryKey: ["platform-plans"],
    queryFn: () => bffGet<PlansListResponse>("/admin/platform/plans"),
    retry: false,
  });

  return (
    <section aria-labelledby="platform-plans-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Platform · Plans"
        titleId="platform-plans-heading"
        description="Reference catalog of usage-governance tiers. Assign or change a tenant's plan from its own detail page."
      />

      {isLoading && <Loading label="Loading plans" className="animate-pulse" />}

      {isError && !isLoading && (
        <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />
      )}

      {!isLoading &&
        !isError &&
        data &&
        (data.plans.length === 0 ? (
          <Empty title="No plan tiers configured." />
        ) : (
          <PlanCardGrid>
            {data.plans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} />
            ))}
          </PlanCardGrid>
        ))}
    </section>
  );
}
