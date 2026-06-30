"use client";

/**
 * BudgetWidget — shows monthly ceiling and current spend from GET /admin/budget.
 *
 * Self-contained edit flow:
 *   canEdit={true}  → "Edit Budget" button rendered inside the widget;
 *                     internal useState manages the form open/close.
 *   canEdit={false} → read-only (member role, or parent wants no edit affordance).
 *
 * The widget is rendered OUTSIDE the tab panel in UsagePage so the "Edit Budget"
 * button stays in the DOM regardless of which tab is active (usage.test.tsx
 * checks for the button after clicking the Records tab).
 */

import { useState } from "react";
import { BffError } from "@/lib/bff-client";
import { BudgetEditForm } from "./BudgetEditForm";
import { Loading, ErrorState, StatCard, Button } from "@/components/ui";

export interface BudgetData {
  budget_usd_monthly: string | null;
  spent_usd_month: string;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

interface BudgetWidgetProps {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  data: BudgetData | undefined;
  /** When true, renders "Edit Budget" button and manages the form internally. */
  canEdit?: boolean;
}

export function BudgetWidget({
  isLoading,
  isError,
  error,
  data,
  canEdit = false,
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
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Monthly Budget" value={ceiling} />
        <StatCard label="Spent this month" value={data.spent_usd_month} />
      </div>
      {canEdit && !isEditing && (
        <Button
          type="button"
          variant="outline"
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
    </div>
  );
}
