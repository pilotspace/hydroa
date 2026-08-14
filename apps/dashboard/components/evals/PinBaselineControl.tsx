"use client";

/**
 * PinBaselineControl — evals-console TASK.md §3 CONTRACT (authoring surface). PUT
 * /admin/evals/sets/{id}/baseline { run_id } pins a run as the eval set's comparison
 * baseline for future verdicts. Failure design (CLAUDE.md "design for failure"): the
 * request is a single bounded fetch through bffPut's own resilient-fetch layer (timeout
 * + retry + per-runtime circuit breaker already live there — no new IO policy needed
 * here); a rejection surfaces inline as role="alert" and NEVER silently drops — the
 * caller's `runs` table is left exactly as it was (no optimistic flip).
 */

import { useState } from "react";
import { bffPut, BffError } from "@/lib/bff-client";
import { Button } from "@/components/ui";
import type { EvalBaselinePutResponse } from "./types";

export interface PinBaselineControlProps {
  setId: string;
  runId: string;
  onPinned: (baselineRunId: string, pinnedAt: number) => void;
}

export function PinBaselineControl({ setId, runId, onPinned }: PinBaselineControlProps) {
  const [isPinning, setIsPinning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handlePin() {
    setIsPinning(true);
    setError(null);
    try {
      const res = await bffPut<EvalBaselinePutResponse>(`/admin/evals/sets/${setId}/baseline`, {
        run_id: runId,
      });
      onPinned(res.baseline_run_id, res.pinned_at);
    } catch (err) {
      if (err instanceof BffError) setError(err.problem.title ?? "Failed to pin baseline");
      else setError("Failed to pin baseline");
    } finally {
      setIsPinning(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" variant="outline" size="sm" onClick={handlePin} disabled={isPinning}>
        {isPinning ? "Pinning…" : "Pin as baseline"}
      </Button>
      {error ? (
        <p role="alert" aria-live="polite" className="text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}
