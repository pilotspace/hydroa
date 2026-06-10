"use client";

/**
 * BudgetEditForm — inline form for PUT /admin/budget
 * Zod validation: empty string → null (clear/unlimited); non-negative decimal only.
 * Renders submit button matching /save|confirm|update|set budget/i.
 * Surfaces 403/422 gateway errors inline without navigation.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiPut, ApiError } from "@/lib/api-client";

const BudgetEditSchema = z
  .string()
  .refine(
    (val) => val === "" || /^\d+(\.\d+)?$/.test(val),
    { message: "Must be a non-negative decimal number" }
  );

interface BudgetEditFormProps {
  currentValue: string | null;
  onCancel: () => void;
}

interface BudgetPutResponse {
  budget_usd_monthly: string | null;
}

function getErrorTitle(err: unknown): string {
  if (err instanceof ApiError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "An error occurred";
}

export function BudgetEditForm({ currentValue, onCancel }: BudgetEditFormProps) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(currentValue ?? "");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (body: { budget_usd_monthly: string | null }) =>
      apiPut<BudgetPutResponse>("/admin/budget", body),
    onSuccess: (data) => {
      // Update the cache directly from the PUT response so the widget reflects
      // the new ceiling immediately without a re-fetch that could return stale data.
      // Merge: keep the current spent_usd_month, update budget_usd_monthly only.
      queryClient.setQueryData(
        ["admin-budget"],
        (old: { budget_usd_monthly: string | null; spent_usd_month: string } | undefined) => ({
          spent_usd_month: old?.spent_usd_month ?? "0.00",
          budget_usd_monthly: data.budget_usd_monthly,
        })
      );
      onCancel();
    },
    onError: (err: unknown) => {
      setServerError(getErrorTitle(err));
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFieldError(null);
    setServerError(null);

    const result = BudgetEditSchema.safeParse(value);
    if (!result.success) {
      setFieldError(result.error.errors[0]?.message ?? "Invalid value");
      return;
    }

    const budgetValue = value.trim() === "" ? null : value.trim();
    mutation.mutate({ budget_usd_monthly: budgetValue });
  }

  return (
    <form onSubmit={handleSubmit}>
      <div>
        <label htmlFor="budget-input">Budget (USD/month)</label>
        <input
          id="budget-input"
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. 50.00 — leave empty for unlimited"
          aria-label="Budget"
        />
        {fieldError && (
          <p role="alert" aria-live="assertive">
            {fieldError}
          </p>
        )}
        {serverError && (
          <p role="alert" aria-live="assertive">
            {serverError}
          </p>
        )}
      </div>
      <div>
        <button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
