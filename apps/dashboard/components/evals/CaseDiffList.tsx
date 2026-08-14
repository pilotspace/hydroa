/**
 * CaseDiffList — evals-console TASK.md §3 CONTRACT M4. Renders one CaseDiffRow per
 * enriched case result. Eval lists are NOT cursor-paginated (design_requirements) — the
 * full array from GET /admin/evals/runs/{run_id}/cases renders, no pagination stack.
 */

import { Empty } from "@/components/ui";
import { CaseDiffRow } from "./CaseDiffRow";
import type { EvalCaseResult } from "./types";

export interface CaseDiffListProps {
  results: EvalCaseResult[];
}

export function CaseDiffList({ results }: CaseDiffListProps) {
  if (results.length === 0) {
    return <Empty title="No case results yet" description="This run has not produced any case results." />;
  }

  return (
    <ul data-testid="case-diff-list" aria-label="Case results" className="flex flex-col gap-3">
      {results.map((result) => (
        <CaseDiffRow key={result.eval_case_id} result={result} />
      ))}
    </ul>
  );
}
