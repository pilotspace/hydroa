/**
 * components/evals/types.ts — hand-written TS interfaces for the evals console
 * (evals-console TASK.md §3 CONTRACT, R7). The gateway wire shapes below are
 * FROZEN — see the task's <execution_context> for the exact contract. Zod is
 * used ONLY for the create-set / add-case dialog INPUTS (CreateSetDialog,
 * AddCaseDialog); every response shape here is a plain hand-written interface.
 */

export interface EvalSetSummary {
  id: string; // "es_<hex>"
  object: "eval.set";
  created_at: number;
  name: string;
  description: string | null;
  case_count: number;
}

export interface EvalSetsListResponse {
  object: "list";
  data: EvalSetSummary[];
}

/** `kind` is the only guaranteed key; every assertion kind may carry its own extra fields
 *  (commonly `expected`), which is why the rest of the shape is `unknown`. */
export interface EvalCaseAssertion {
  kind: string;
  [key: string]: unknown;
}

export interface EvalCase {
  id: string; // "ec_<hex>"
  object: "eval.case";
  created_at: number;
  eval_set_id: string;
  assertion: EvalCaseAssertion;
}

export type EvalRunStatus = "pending" | "running" | "completed" | "failed";

export interface EvalRun {
  id: string; // "er_<hex>"
  object: "eval.run";
  created_at: number;
  eval_set_id: string;
  model: string;
  status: EvalRunStatus;
  case_count: number;
}

export interface EvalSetDetail {
  id: string;
  object: "eval.set";
  created_at: number;
  name: string;
  description: string | null;
  cases: EvalCase[];
  runs: EvalRun[];
  baseline_run_id: string | null;
}

export interface EvalScore {
  passed: number;
  total: number;
}

export type EvalVerdictOutcome = "pass" | "fail" | "no_baseline";

export interface EvalVerdict {
  object: "eval.verdict";
  run_id: string;
  score: EvalScore;
  baseline: { run_id: string; score: EvalScore } | null;
  verdict: EvalVerdictOutcome;
}

/** A row from the ENRICHED GET /admin/evals/runs/{run_id}/cases — one per snapshot case,
 *  joined with its result. "completed" can still be a FAILED assertion, so a completed row
 *  carries `passed` — the AUTHORITATIVE per-case verdict from the same deterministic scorer
 *  the run verdict counts with. The UI renders that bool directly; it must NEVER re-derive
 *  pass/fail from the payload (that would fork scoring and disagree with the banner for e.g.
 *  a `contains` assertion). A non-"completed" row NEVER carries `response_text` or `passed`
 *  — rendering code must never fabricate one. */
export type EvalCaseResultStatus = "completed" | "refused" | "errored" | "pending";

export interface EvalCaseResult {
  eval_case_id: string;
  assertion: EvalCaseAssertion;
  status: EvalCaseResultStatus;
  response_text?: string;
  reason?: string;
  /** Present (and meaningful) only for a "completed" row — the scorer's verdict for this case. */
  passed?: boolean;
}

export interface EvalCaseResultsResponse {
  object: "list";
  data: EvalCaseResult[];
}

export interface EvalBaselinePutResponse {
  object: "eval.baseline";
  eval_set_id: string;
  baseline_run_id: string;
  pinned_at: number;
}
