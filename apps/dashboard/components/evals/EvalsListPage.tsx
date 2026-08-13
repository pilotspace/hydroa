"use client";

/**
 * EvalsListPage — evals-console TASK.md §3 CONTRACT M1/M5/M6/M9. Top of the verdict-
 * first IA: Sets list -> Set detail -> Run verdict. GET /admin/evals/sets is NOT
 * cursor-paginated (design_requirements) — the full returned list renders, no
 * pagination stack (mirrors the AgentsConsolePage idiom rather than InvoicesListPage's
 * cursor stack, since this list has no cursor at all).
 *
 * M6 identity gate: see RunVerdictPage's own docblock — identical reasoning, applied
 * here to the sets query.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Button, Empty, ErrorState, Loading, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { useCurrentUser } from "@/lib/hooks/use-current-user";
import { CreateSetDialog } from "./CreateSetDialog";
import { formatEpochSeconds } from "./format";
import { getEvalsErrorTitle } from "./errors";
import type { EvalSetSummary, EvalSetsListResponse } from "./types";

const SUBSYSTEM = "Evals";
const EVAL_SETS_QUERY_KEY = ["eval-sets"];

export function EvalsListPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: currentUser, isLoading: identityLoading } = useCurrentUser();
  const identityReady = !identityLoading && currentUser !== null;
  const [createOpen, setCreateOpen] = useState(false);

  const setsQuery = useQuery<EvalSetsListResponse>({
    queryKey: EVAL_SETS_QUERY_KEY,
    queryFn: () => bffGet<EvalSetsListResponse>("/admin/evals/sets"),
    retry: false,
    enabled: identityReady,
  });

  // M6 fail-closed identity gate — a visible Loading indicator, never a null-leak.
  if (!identityReady) {
    return <Loading label="Loading…" />;
  }

  const items = setsQuery.data?.data ?? [];

  function handleCreated(set: EvalSetSummary) {
    queryClient.setQueryData<EvalSetsListResponse>(EVAL_SETS_QUERY_KEY, (prev) =>
      prev ? { ...prev, data: [...prev.data, set] } : { object: "list", data: [set] },
    );
  }

  return (
    <section aria-labelledby="evals-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Evals"
        titleId="evals-heading"
        description="Regression gates for your model configuration — verdict-first, with a per-case diff on drill-down."
        actions={
          <Button type="button" onClick={() => setCreateOpen(true)}>
            New eval set
          </Button>
        }
      />

      <div className="rounded-lg border border-border bg-muted/20 p-3 text-sm text-muted-foreground">
        Runs are launched via the <code className="font-mono">/v1</code> API key — this console reads
        results, it never starts a run.
      </div>

      {setsQuery.isLoading ? (
        <Loading label="Loading eval sets…" />
      ) : setsQuery.isError ? (
        <ErrorState title={getEvalsErrorTitle(setsQuery.error, SUBSYSTEM)} onRetry={() => setsQuery.refetch()} />
      ) : items.length === 0 ? (
        <Empty
          title="No eval sets yet"
          description="Create an eval set to start tracking a regression gate."
          action={
            <Button type="button" onClick={() => setCreateOpen(true)}>
              New eval set
            </Button>
          }
        />
      ) : (
        <Table aria-label="Eval sets">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="text-right">Cases</TableHead>
              <TableHead>Created</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((set) => (
              <TableRow
                key={set.id}
                tabIndex={0}
                aria-label={`Eval set ${set.name} · ${set.case_count} cases`}
                onClick={() => router.push(`/app/evals/${set.id}`)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    router.push(`/app/evals/${set.id}`);
                  }
                }}
                className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
              >
                <TableCell>
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">{set.name}</span>
                    {set.description ? (
                      <span className="text-xs text-muted-foreground">{set.description}</span>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-right font-mono tabular-nums">{set.case_count}</TableCell>
                <TableCell>{formatEpochSeconds(set.created_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateSetDialog open={createOpen} onClose={() => setCreateOpen(false)} onCreated={handleCreated} />
    </section>
  );
}
