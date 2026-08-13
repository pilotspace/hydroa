"use client";

/**
 * RunVerdictPage — evals-console TASK.md §3 CONTRACT M3/M4/M6, the verdict-first leaf of
 * the IA (Sets -> Set detail -> Run verdict). Fetches GET .../verdict and the ENRICHED
 * GET .../cases in PARALLEL (never a waterfall), then leads with <VerdictBanner> BEFORE
 * <CaseDiffList> in DOM order (R:VERDICT_NOT_PRIMARY) — the page is not a row count.
 *
 * M6 / R:NULL_RENDER_LEAK identity gate: while the caller's OWN identity
 * (useCurrentUser()) is still loading or unresolved, this page renders NOTHING
 * actionable — no PageHeader, no banner, no table — but it is NEVER a null/blank
 * render either: a <Loading role="status"> is always on screen so the surface is never
 * a silent void while identity resolves. Both data queries stay `enabled: false` until
 * identity is known, so no query fires (and no query cache entry with role-less data is
 * ever created) before that gate opens.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { CaseDiffList } from "./CaseDiffList";
import { VerdictBanner, computeBannerState } from "./VerdictBanner";
import { getEvalsErrorTitle } from "./errors";
import type { EvalCaseResultsResponse, EvalVerdict } from "./types";

export interface RunVerdictPageProps {
  setId: string;
  runId: string;
}

const SUBSYSTEM = "Evals";

export function RunVerdictPage({ setId, runId }: RunVerdictPageProps) {
  const { data: currentUser, isLoading: identityLoading } = useCurrentUser();
  const identityReady = !identityLoading && currentUser !== null;

  const verdictQuery = useQuery<EvalVerdict>({
    queryKey: ["eval-run-verdict", runId],
    queryFn: () => bffGet<EvalVerdict>(`/admin/evals/runs/${runId}/verdict`),
    retry: false,
    enabled: identityReady,
  });

  const casesQuery = useQuery<EvalCaseResultsResponse>({
    queryKey: ["eval-run-cases", runId],
    queryFn: () => bffGet<EvalCaseResultsResponse>(`/admin/evals/runs/${runId}/cases`),
    retry: false,
    enabled: identityReady,
  });

  // M6 fail-closed identity gate — NEVER a component that returns null while identity
  // resolves; a visible Loading indicator stands in for the whole surface instead.
  if (!identityReady) {
    return <Loading label="Loading…" />;
  }

  const isLoading = verdictQuery.isLoading || casesQuery.isLoading;
  const error = verdictQuery.error ?? casesQuery.error;
  const verdict = verdictQuery.data;
  const cases = casesQuery.data?.data;

  return (
    <section aria-labelledby="run-verdict-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Run verdict"
        titleId="run-verdict-heading"
        description={verdict ? `Set ${setId} · Run ${runId}` : undefined}
      />

      {isLoading ? (
        <Loading label="Loading run verdict…" />
      ) : error ? (
        <ErrorState title={getEvalsErrorTitle(error, SUBSYSTEM)} onRetry={() => { verdictQuery.refetch(); casesQuery.refetch(); }} />
      ) : verdict && cases ? (
        <>
          <VerdictBanner state={computeBannerState(verdict, cases)} verdict={verdict} />
          <CaseDiffList results={cases} />
        </>
      ) : null}
    </section>
  );
}
