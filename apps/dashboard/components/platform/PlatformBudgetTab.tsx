"use client";

/**
 * PlatformBudgetTab — Budget tab (M7) of the tenant-detail screen: a StatCard
 * pair (Monthly Budget / Spent this month) plus an "Edit Budget" affordance,
 * parametrized by the TARGET tenant_id.
 *
 * GET/PUT .../{tenantId}/budget -> BudgetGetResponse/BudgetPutRequest
 * {budget_usd_monthly: str|null, spent_usd_month: str} — byte-identical field
 * names to gateway/budgets/api/schemas.py (TASK.md §0 GROUND, confirmed by
 * reading it directly). Validation mirrors TeamBudgetForm.tsx's
 * validateBudget EXACTLY (empty clears the budget; a non-numeric, zero, or
 * negative value is blocked client-side before any request fires — M7's own
 * explicit "non-numeric/zero/negative... blocked" wording, stricter than
 * BudgetEditForm.tsx's self-service Zod schema, which does not reject zero).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet, bffPut, BffError } from "@/lib/bff-client";
import { Input, Button, Loading, ErrorState, StatCard } from "@/components/ui";

export interface BudgetData {
  budget_usd_monthly: string | null;
  spent_usd_month: string;
}

type BudgetValidation = { value: string | null } | { error: string };

/** Mirrors TeamBudgetForm.tsx's validateBudget exactly: empty clears (null);
 * otherwise must be a positive decimal (<=2 dp) — zero/negative/non-numeric
 * rejected client-side, no request fires. */
function validateBudget(raw: string): BudgetValidation {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null };
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed) || Number(trimmed) <= 0) {
    return { error: "Enter a positive amount" };
  }
  return { value: trimmed };
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export interface PlatformBudgetTabProps {
  tenantId: string;
}

export function PlatformBudgetTab({ tenantId }: PlatformBudgetTabProps) {
  const queryClient = useQueryClient();
  const queryKey = ["platform-tenant-budget", tenantId];

  const { data, isLoading, isError, error, refetch } = useQuery<BudgetData>({
    queryKey,
    queryFn: () => bffGet<BudgetData>(`/admin/platform/tenants/${tenantId}/budget`),
    retry: false,
  });

  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const saveBudget = useMutation({
    mutationFn: (budget_usd_monthly: string | null) =>
      bffPut<{ budget_usd_monthly: string | null }>(
        `/admin/platform/tenants/${tenantId}/budget`,
        { budget_usd_monthly },
      ),
    onSuccess: (resp) => {
      setFieldError(null);
      setServerError(null);
      // Merge, never clobber: the PUT response only echoes budget_usd_monthly —
      // spent_usd_month is preserved from the current cache entry.
      queryClient.setQueryData<BudgetData>(queryKey, (old) => ({
        spent_usd_month: old?.spent_usd_month ?? "0.00",
        budget_usd_monthly: resp.budget_usd_monthly,
      }));
      setIsEditing(false);
    },
    onError: (err) => {
      setServerError(err instanceof BffError ? err.problem.title : getErrorTitle(err));
    },
  });

  function openEdit() {
    setDraft(data?.budget_usd_monthly ?? "");
    setFieldError(null);
    setServerError(null);
    setIsEditing(true);
  }

  function handleSave() {
    setFieldError(null);
    setServerError(null);
    const result = validateBudget(draft);
    if ("error" in result) {
      setFieldError(result.error);
      return;
    }
    saveBudget.mutate(result.value);
  }

  if (isLoading) {
    return <Loading label="Loading budget" className="animate-pulse" />;
  }
  if (isError) {
    return <ErrorState title={getErrorTitle(error)} onRetry={() => void refetch()} />;
  }
  if (!data) return null;

  const ceiling = data.budget_usd_monthly === null ? "Unlimited" : data.budget_usd_monthly;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Monthly Budget" value={ceiling} variant="flat" />
        <StatCard label="Spent this month" value={data.spent_usd_month} variant="flat" />
      </div>

      {!isEditing && (
        <div>
          <Button
            type="button"
            variant="outline"
            className="rounded-[var(--radius-flat-control)]"
            onClick={openEdit}
          >
            Edit Budget
          </Button>
        </div>
      )}

      {isEditing && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label htmlFor="platform-budget-input" className="text-sm font-medium text-foreground">
              Budget
            </label>
            <Input
              id="platform-budget-input"
              type="text"
              inputMode="decimal"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="e.g. 50.00 — leave empty for unlimited"
              aria-label="Budget"
              className="rounded-[var(--radius-flat-control)]"
            />
            {fieldError && (
              <p role="alert" aria-live="assertive" className="text-sm text-destructive">
                {fieldError}
              </p>
            )}
            {serverError && (
              <p role="alert" aria-live="assertive" className="text-sm text-destructive">
                {serverError}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              className="rounded-[var(--radius-flat-control)]"
              disabled={saveBudget.isPending}
              onClick={handleSave}
            >
              {saveBudget.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              type="button"
              variant="outline"
              className="rounded-[var(--radius-flat-control)]"
              onClick={() => setIsEditing(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
