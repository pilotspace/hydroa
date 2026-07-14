"use client";

/**
 * TeamBudgetForm — inline per-row budget editor (PATCH /admin/teams/{id}).
 *
 * team_budget_usd is a decimal-as-STRING; an empty input CLEARS the budget (null).
 * A non-numeric / zero / negative value is blocked client-side (no request). On
 * success the row's budget cell updates directly from the PATCH response via
 * setQueryData — the gateway echoes the authoritative new value — so the cell
 * reflects the change without depending on a refetch. The Save button is disabled
 * while the request is in flight.
 *
 * Re-seed on external value change (stale-save fix): `draft` seeds from the `team`
 * prop but is otherwise LOCAL — a plain mount-only useState never re-reads a changed
 * prop. Paired with TeamsPage's DataTable `getRowId={t => t.id}` (a stable per-team
 * React key so a reordered/shrunk list remounts this form on true identity change,
 * rather than reusing a stale instance whose draft belonged to a DIFFERENT team), this
 * component ALSO re-seeds when the SAME team's server value changes underneath it
 * (e.g. another admin's edit lands via a background refetch) — but only when the
 * user hasn't started an in-flight edit, via the "adjust state during render" idiom
 * used elsewhere in this codebase (SettingsPage, InvoiceEvidenceDrawer): compare the
 * incoming prop to the last-seen server value, and only overwrite `draft` when it
 * still equals that last-seen value (untouched). An in-flight keystroke the user is
 * mid-typing is NEVER clobbered by an unrelated re-render.
 */

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bffPatch, BffError } from "@/lib/bff-client";
import { Input, Button } from "@/components/ui";
import type { TeamResponse } from "./types";

type BudgetResult = { value: string | null } | { error: string };

function validateBudget(raw: string): BudgetResult {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null }; // clear the budget
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed) || Number(trimmed) <= 0) {
    return { error: "Enter a positive amount" };
  }
  return { value: trimmed };
}

interface TeamBudgetFormProps {
  team: TeamResponse;
}

export function TeamBudgetForm({ team }: TeamBudgetFormProps) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(team.team_budget_usd ?? "");
  const [error, setError] = useState<string | null>(null);
  const [lastSeenBudget, setLastSeenBudget] = useState(team.team_budget_usd);

  // Re-seed the draft when the SERVER value changes for this same row identity (e.g. an
  // external update landing via refetch), but ONLY when the user hasn't started an
  // untouched-since-last-seen edit — never clobber in-flight keystrokes.
  if (team.team_budget_usd !== lastSeenBudget) {
    if (draft === (lastSeenBudget ?? "")) {
      setDraft(team.team_budget_usd ?? "");
    }
    setLastSeenBudget(team.team_budget_usd);
  }

  const patchBudget = useMutation({
    mutationFn: (team_budget_usd: string | null) =>
      bffPatch<TeamResponse>(`/admin/teams/${team.id}`, { team_budget_usd }),
    onSuccess: (updated) => {
      setError(null);
      queryClient.setQueryData<TeamResponse[]>(
        ["admin-teams"],
        (old) => old?.map((t) => (t.id === updated.id ? updated : t)) ?? old,
      );
    },
    onError: (err) => {
      // A failed PATCH must never fail silently (§5 safety rule): surface the title.
      setError(err instanceof BffError ? err.problem.title : "Failed to save budget");
    },
  });

  function handleSave() {
    setError(null);
    const result = validateBudget(draft);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    patchBudget.mutate(result.value);
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="min-w-14 text-sm tabular-nums text-foreground">
          {team.team_budget_usd ?? "—"}
        </span>
        <Input
          aria-label={`Budget for ${team.name}`}
          inputMode="decimal"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handleSave();
            }
          }}
          placeholder="0.00"
          className="h-8 w-28"
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={`Save budget for ${team.name}`}
          disabled={patchBudget.isPending}
          onClick={handleSave}
        >
          {patchBudget.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      {error && (
        <p role="alert" aria-live="polite" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
