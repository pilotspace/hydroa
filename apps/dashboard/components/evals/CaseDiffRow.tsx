/**
 * CaseDiffRow — evals-console TASK.md §3 CONTRACT M4, the SIGNATURE element of this
 * whole console: an expected-vs-actual DIFF per case, never a bare colored dot.
 *
 * GET /admin/evals/runs/{run_id}/cases is the ENRICHED, frozen contract: a "completed"
 * row can still have FAILED its assertion, so it carries `passed` — the AUTHORITATIVE
 * per-case verdict from the same deterministic scorer the run verdict counts with. This
 * component renders that bool directly and NEVER re-derives pass/fail from the payload:
 * a client-side re-implementation would fork scoring and, for a `contains` assertion,
 * disagree with the banner (expected "echo" vs actual "echo:one" is a PASS the scorer
 * sees but string equality would miss). A "refused"/"errored"/"pending" row carries NO
 * response_text and NO `passed` — this component renders status + reason for those and
 * NEVER fabricates an "actual" value.
 */

import { EvalStatusBadge } from "./EvalStatusBadge";
import { formatExpected } from "./format";
import type { EvalCaseResult } from "./types";

export type CaseOutcome = "pass" | "fail" | "refused" | "errored" | "pending";

/**
 * Map a case row to its DISPLAY outcome. "refused"/"errored"/"pending" pass straight
 * through (no verdict to show); a "completed" row shows the backend's authoritative
 * `passed` bool — pass/fail is never recomputed client-side. A completed row missing
 * `passed` (should not happen on the frozen contract) degrades defensively to "fail".
 */
export function deriveCaseOutcome(result: EvalCaseResult): CaseOutcome {
  if (result.status === "refused") return "refused";
  if (result.status === "errored") return "errored";
  if (result.status === "pending") return "pending";
  return result.passed === true ? "pass" : "fail";
}

export interface CaseDiffRowProps {
  result: EvalCaseResult;
}

export function CaseDiffRow({ result }: CaseDiffRowProps) {
  const outcome = deriveCaseOutcome(result);
  const expectedDisplay = formatExpected(result.assertion);
  const isCompleted = result.status === "completed";

  return (
    <li
      data-testid={`case-diff-${result.eval_case_id}`}
      className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">{result.eval_case_id}</span>
        <EvalStatusBadge status={outcome} />
      </div>

      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Assertion · <span className="font-mono normal-case text-foreground">{result.assertion.kind}</span>
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Expected</p>
          <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-xs text-foreground">
            {expectedDisplay}
          </pre>
        </div>
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Actual</p>
          {isCompleted ? (
            <pre className="whitespace-pre-wrap break-words rounded-md bg-muted/40 p-2 font-mono text-xs text-foreground">
              {result.response_text ?? "—"}
            </pre>
          ) : (
            <p className="rounded-md bg-muted/40 p-2 text-xs italic text-muted-foreground">
              No response — case {result.status}
            </p>
          )}
        </div>
      </div>

      {result.reason ? (
        <p className="text-sm text-destructive-text">{result.reason}</p>
      ) : null}
    </li>
  );
}
