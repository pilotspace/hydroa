"use client";

/**
 * BatchesStatsPage — the dashboard Batches surface (admin-only nav). Read-only
 * tenant-wide statistics from GET /admin/batches/stats: savings + volume +
 * status breakdown. No composer, no submission, no toggle — those live in the
 * separate batch-auto-grouping task.
 *
 * Modeled directly on AuditPage.tsx's minimal shape (single query, no Tabs) with
 * one addition: an app-level Empty check (total_requests === 0) rendered
 * ALONGSIDE the honest-zero StatCards, not instead of them — "genuinely nothing
 * yet" is a distinct signal from "the query failed" (TASK.md §2).
 *
 * Auth guard: proxy.ts handles cookie-presence server-side; the admin-only nav
 * link and the gateway's require_owner_or_admin enforcement keep other roles out.
 */

import { useQuery } from "@tanstack/react-query";
import { getBatchStats, type BatchStatsData } from "@/lib/batches";
import { Badge, Loading, ErrorState, Empty, StatCard } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { BffError } from "@/lib/bff-client";

function getErrorTitle(err: unknown): string {
  if (err instanceof BffError) return err.problem.title;
  if (err instanceof Error) return err.message;
  return "Failed to load batch statistics";
}

export function BatchesStatsPage() {
  const statsQuery = useQuery<BatchStatsData>({
    queryKey: ["admin-batches-stats"],
    queryFn: () => getBatchStats(),
  });

  const data = statsQuery.data;
  const isEmpty = data !== undefined && data.total_requests === 0;

  return (
    <section aria-labelledby="batches-heading" className="flex flex-col gap-6">
      <PageHeader
        title="Batches"
        titleId="batches-heading"
        description="How much this tenant is saving by processing eligible requests as discounted batch jobs instead of standard synchronous calls, and how that traffic is moving through the pipeline."
      />

      <div className="flex items-center gap-2">
        <Badge variant="warning">Experimental</Badge>
        <p className="text-sm text-muted-foreground">
          Batch processing is still being rolled out — some of the numbers below are
          honest placeholders, not live measurements yet.
        </p>
      </div>

      {statsQuery.isLoading ? (
        <Loading label="Loading batch statistics…" />
      ) : statsQuery.isError ? (
        <ErrorState
          title={getErrorTitle(statsQuery.error)}
          onRetry={() => statsQuery.refetch()}
        />
      ) : data ? (
        <>
          <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Total saved via batching
              </p>
              <p
                data-testid="batches-savings-value"
                className="text-4xl font-semibold tracking-tight text-foreground"
              >
                ${data.savings_usd}
              </p>
              <p className="mt-2 max-w-[46ch] text-sm text-muted-foreground">
                An honest $0.00 until real batch traffic has been priced.
              </p>
            </div>
            <div className="max-w-[260px] rounded-lg border border-accent-soft-border bg-accent-soft px-3 py-2 text-xs text-primary">
              $0.00 is accurate today — real batch traffic hasn&apos;t been priced yet. This
              number updates itself once it has, no page change needed.
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard label="Batched requests" value={String(data.total_requests)} />
            <StatCard label="Succeeded" value={String(data.status_counts.succeeded)} />
            <StatCard label="Errored" value={String(data.status_counts.errored)} />
            <StatCard label="In progress" value={String(data.status_counts.pending)} />
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <StatCard
              label="Avg. queue wait"
              value={
                data.avg_window_wait_ms != null
                  ? `${Math.round(data.avg_window_wait_ms)} ms`
                  : "—"
              }
              footer={data.avg_window_wait_ms != null ? null : "No windows have flushed yet."}
            />
            <StatCard
              label="Avg. completion latency"
              value="—"
              footer="Lands once real batch processing exists (v58)."
            />
            <StatCard
              label="Token throughput (TPM)"
              value="—"
              footer="Lands once real batch processing exists (v58)."
            />
          </div>

          {isEmpty ? (
            <Empty
              title="No batch activity yet"
              description="This tenant hasn't had any requests grouped into a batch job yet. Numbers above are honest zeros, not placeholders — they'll move on their own once batching is active."
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
