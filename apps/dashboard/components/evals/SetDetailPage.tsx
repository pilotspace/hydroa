"use client";

/**
 * SetDetailPage — evals-console TASK.md §3 CONTRACT M1/M5/M6/M9. The middle of the IA:
 * an eval set's cases, its runs (clickable rows -> the run's verdict page, keyboard
 * navigable per M5), and its pinned baseline. Launch is explicitly NOT handled here —
 * an informational note only ("launch via the /v1 API key"), never a launch button,
 * never a raw key.
 *
 * M6 identity gate: identical reasoning to EvalsListPage/RunVerdictPage.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Button, Empty, ErrorState, Loading, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { AddCaseDialog } from "./AddCaseDialog";
import { EvalStatusBadge } from "./EvalStatusBadge";
import { PinBaselineControl } from "./PinBaselineControl";
import { formatEpochSeconds } from "./format";
import { getEvalsErrorTitle } from "./errors";
import type { EvalCase, EvalSetDetail } from "./types";

const SUBSYSTEM = "Evals";

export interface SetDetailPageProps {
  setId: string;
}

function setDetailQueryKey(setId: string) {
  return ["eval-set-detail", setId];
}

export function SetDetailPage({ setId }: SetDetailPageProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: currentUser, isLoading: identityLoading } = useCurrentUser();
  const identityReady = !identityLoading && currentUser !== null;
  const [addCaseOpen, setAddCaseOpen] = useState(false);

  const setQuery = useQuery<EvalSetDetail>({
    queryKey: setDetailQueryKey(setId),
    queryFn: () => bffGet<EvalSetDetail>(`/admin/evals/sets/${setId}`),
    retry: false,
    enabled: identityReady,
  });

  // M6 fail-closed identity gate — a visible Loading indicator, never a null-leak.
  if (!identityReady) {
    return <Loading label="Loading…" />;
  }

  const detail = setQuery.data;

  function handleCaseCreated(newCase: EvalCase) {
    queryClient.setQueryData<EvalSetDetail>(setDetailQueryKey(setId), (prev) =>
      prev ? { ...prev, cases: [...prev.cases, newCase] } : prev,
    );
  }

  function handleBaselinePinned(baselineRunId: string) {
    queryClient.setQueryData<EvalSetDetail>(setDetailQueryKey(setId), (prev) =>
      prev ? { ...prev, baseline_run_id: baselineRunId } : prev,
    );
  }

  function goToRun(runId: string) {
    router.push(`/app/evals/${setId}/runs/${runId}`);
  }

  return (
    <section aria-labelledby="eval-set-heading" className="flex flex-col gap-6">
      <PageHeader
        title={detail?.name ?? "Eval set"}
        titleId="eval-set-heading"
        description={detail?.description ?? undefined}
        actions={
          detail ? (
            <Button type="button" onClick={() => setAddCaseOpen(true)}>
              Add case
            </Button>
          ) : undefined
        }
      />

      {setQuery.isLoading ? (
        <Loading label="Loading eval set…" />
      ) : setQuery.isError ? (
        <ErrorState title={getEvalsErrorTitle(setQuery.error, SUBSYSTEM)} onRetry={() => setQuery.refetch()} />
      ) : detail ? (
        <>
          <div className="rounded-lg border border-border bg-muted/20 p-3 text-sm text-muted-foreground">
            Runs are launched via the <code className="font-mono">/v1</code> API key — this console reads
            results, it never starts a run.
          </div>

          <div className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-foreground">Cases ({detail.cases.length})</h2>
            {detail.cases.length === 0 ? (
              <Empty
                title="No cases yet"
                description="Add a case to start building this eval set."
                action={
                  <Button type="button" onClick={() => setAddCaseOpen(true)}>
                    Add case
                  </Button>
                }
              />
            ) : (
              <Table aria-label="Eval cases">
                <TableHeader>
                  <TableRow>
                    <TableHead>Case</TableHead>
                    <TableHead>Assertion</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.cases.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="font-mono text-xs text-muted-foreground">{c.id}</TableCell>
                      <TableCell className="font-mono text-xs">{c.assertion.kind}</TableCell>
                      <TableCell>{formatEpochSeconds(c.created_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>

          <div className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-foreground">Runs ({detail.runs.length})</h2>
            {detail.runs.length === 0 ? (
              <Empty
                title="No runs yet"
                description="Launch a run via the /v1 API key — results will appear here."
              />
            ) : (
              <Table aria-label="Eval runs">
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Cases</TableHead>
                    <TableHead>Baseline</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.runs.map((run) => {
                    const isBaseline = detail.baseline_run_id === run.id;
                    return (
                      <TableRow
                        key={run.id}
                        tabIndex={0}
                        aria-label={`Run ${run.model} · ${run.status}`}
                        onClick={() => goToRun(run.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            goToRun(run.id);
                          }
                        }}
                        className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                      >
                        <TableCell className="font-mono text-xs">{run.model}</TableCell>
                        <TableCell>
                          <EvalStatusBadge status={run.status} />
                        </TableCell>
                        <TableCell className="text-right font-mono tabular-nums">{run.case_count}</TableCell>
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          {isBaseline ? (
                            <Badge variant="secondary">Baseline</Badge>
                          ) : (
                            <PinBaselineControl setId={setId} runId={run.id} onPinned={handleBaselinePinned} />
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </div>
        </>
      ) : null}

      <AddCaseDialog open={addCaseOpen} onClose={() => setAddCaseOpen(false)} setId={setId} onCreated={handleCaseCreated} />
    </section>
  );
}
