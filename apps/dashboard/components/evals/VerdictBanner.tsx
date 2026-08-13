/**
 * VerdictBanner — evals-console TASK.md §3 CONTRACT M3 / R:VERDICT_NOT_PRIMARY. The
 * FIRST thing RunVerdictPage renders in DOM order, before any results listing — the
 * page is not a row count.
 *
 * Four distinct states, each with its own icon + text (never color alone):
 *   pass        — role="status", candidate vs baseline scores
 *   fail        — role="alert" (a regression is worth interrupting a screen reader for)
 *   no_baseline — role="status", explicit "no baseline pinned" copy — NEVER pass-styled
 *   pending     — role="status", "run pending" — computeBannerState forces this whenever
 *                 ANY case in the run is still "pending", regardless of what the verdict
 *                 endpoint reports (a verdict computed from a partial case set is not
 *                 final — design_requirements EDGES).
 */

import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import type { EvalCaseResult, EvalVerdict } from "./types";

export type BannerState = "pass" | "fail" | "no_baseline" | "pending";

/**
 * The single source of truth for which of the four banner states to show. A pending
 * case in the run ALWAYS wins over whatever the verdict endpoint itself reports (it may
 * have computed a partial score) — never a green/red verdict while cases are still in
 * flight.
 */
export function computeBannerState(verdict: EvalVerdict, cases: EvalCaseResult[]): BannerState {
  if (cases.some((c) => c.status === "pending")) return "pending";
  if (verdict.verdict === "no_baseline") return "no_baseline";
  return verdict.verdict;
}

export interface VerdictBannerProps {
  state: BannerState;
  verdict: EvalVerdict;
}

export function VerdictBanner({ state, verdict }: VerdictBannerProps) {
  if (state === "pending") {
    return (
      <div
        data-testid="verdict-banner"
        role="status"
        aria-live="polite"
        className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4"
      >
        <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-foreground">Run pending</p>
          <p className="text-sm text-muted-foreground">
            Cases are still executing — the verdict is not final yet.
          </p>
        </div>
      </div>
    );
  }

  if (state === "no_baseline") {
    return (
      <div
        data-testid="verdict-banner"
        role="status"
        className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 p-4"
      >
        <CircleDashed className="size-5 text-muted-foreground" aria-hidden="true" />
        <div>
          <p className="text-sm font-semibold text-foreground">No baseline pinned</p>
          <p className="text-sm text-muted-foreground">
            Candidate {verdict.score.passed}/{verdict.score.total} passed — pin a baseline run on the eval
            set to compare regressions.
          </p>
        </div>
      </div>
    );
  }

  const isPass = state === "pass";
  return (
    <div
      data-testid="verdict-banner"
      role={isPass ? "status" : "alert"}
      className={cn(
        "flex items-center gap-3 rounded-lg border p-4",
        isPass ? "border-success/30 bg-success/5" : "border-destructive/30 bg-destructive/5",
      )}
    >
      {isPass ? (
        <CheckCircle2 className="size-5 text-success-text" aria-hidden="true" />
      ) : (
        <XCircle className="size-5 text-destructive-text" aria-hidden="true" />
      )}
      <div>
        <p className={cn("text-sm font-semibold", isPass ? "text-success-text" : "text-destructive-text")}>
          {isPass ? "Pass" : "Fail"}
        </p>
        <p className="text-sm text-foreground">
          Candidate{" "}
          <span className="font-mono tabular-nums">
            {verdict.score.passed}/{verdict.score.total}
          </span>
          {verdict.baseline ? (
            <>
              {" "}
              · Baseline{" "}
              <span className="font-mono tabular-nums">
                {verdict.baseline.score.passed}/{verdict.baseline.score.total}
              </span>
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}
