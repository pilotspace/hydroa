"use client";

/**
 * BudgetWidget — shows monthly ceiling and current spend from GET /admin/budget.
 * Owner/admin: "Edit Budget" button opens BudgetEditForm inline.
 * Member: read-only (no edit affordance).
 * null ceiling renders as "Unlimited".
 */

import { useState } from "react";
import { ApiError } from "@/lib/api-client";
import { BudgetEditForm } from "./BudgetEditForm";
import { Card, CardContent, Button, Loading, ErrorState } from "@/components/ui";

export interface BudgetData {
  budget_usd_monthly: string | null;
  spent_usd_month: string;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof ApiError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

interface BudgetWidgetProps {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data: BudgetData | undefined;
  canEdit: boolean;
}

export function BudgetWidget({
  isLoading,
  isError,
  error,
  data,
  canEdit,
}: BudgetWidgetProps) {
  const [isEditing, setIsEditing] = useState(false);

  if (isLoading) {
    return <Loading label="Loading budget" className="animate-pulse" />;
  }

  if (isError) {
    return <ErrorState title={getErrorTitle(error)} />;
  }

  if (!data) return null;

  const ceiling =
    data.budget_usd_monthly === null ? "Unlimited" : data.budget_usd_monthly;

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="text-sm text-foreground">
          <span className="text-muted-foreground">Monthly Budget: </span>
          <span className="font-semibold">{ceiling}</span>
        </div>
        <div className="text-sm text-foreground">
          <span className="text-muted-foreground">Spent this month: </span>
          <span className="font-semibold">{data.spent_usd_month}</span>
        </div>
        {canEdit && !isEditing && (
          <Button
            type="button"
            variant="outline"
            className="self-start"
            onClick={() => setIsEditing(true)}
          >
            Edit Budget
          </Button>
        )}
        {isEditing && (
          <BudgetEditForm
            currentValue={data.budget_usd_monthly}
            onCancel={() => setIsEditing(false)}
          />
        )}
      </CardContent>
    </Card>
  );
}
