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
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label="Loading budget"
        className="animate-pulse"
      >
        <span>Loading budget…</span>
      </div>
    );
  }

  if (isError) {
    return <p role="alert">{getErrorTitle(error)}</p>;
  }

  if (!data) return null;

  const ceiling =
    data.budget_usd_monthly === null ? "Unlimited" : data.budget_usd_monthly;

  return (
    <div>
      <div>
        <span>Monthly Budget: </span>
        <span>{ceiling}</span>
      </div>
      <div>
        <span>Spent this month: </span>
        <span>{data.spent_usd_month}</span>
      </div>
      {canEdit && !isEditing && (
        <button
          type="button"
          onClick={() => setIsEditing(true)}
        >
          Edit Budget
        </button>
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
