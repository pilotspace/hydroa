"use client";

/**
 * HealthPage — PageHeader + hero + a single (untabbed) upstream status view.
 *
 * audit-remediation (item 6): this page used to tab between "Overview" and a
 * permanent "History" stub ("not available yet"). GET /admin/health/upstreams
 * (gateway/alerting/application/health_checker.py) only ever returns a live
 * snapshot — {checked_at, upstreams:[...]} — there is no time-series/history
 * data source behind it, and adding one is a backend change out of this
 * package's scope. Rather than leave a tab that can never show real content
 * (a dead end masquerading as a feature), the History tab was REMOVED and
 * Overview's content is now rendered directly. Re-add a History tab only once
 * a real history endpoint exists to back it.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { UpstreamsTable, UpstreamHealthData } from "./UpstreamsTable";
import { PageHeader } from "@/components/ui/page-header";

export function HealthPage() {
  const healthQuery = useQuery<UpstreamHealthData>({
    queryKey: ["admin-upstream-health"],
    queryFn: () => bffGet<UpstreamHealthData>("/admin/health/upstreams"),
  });

  const upCount = healthQuery.data?.upstreams.filter((u) => u.status === "up").length ?? 0;
  const totalCount = healthQuery.data?.upstreams.length ?? 0;

  return (
    <section
      aria-labelledby="health-heading"
      className="flex flex-col gap-6"
    >
      <PageHeader
        title="Upstream Health"
        titleId="health-heading"
        description="Live status of your model providers"
      />

      {/* Hero region — N/M upstreams healthy */}
      {healthQuery.data && (
        <div
          data-testid="health-hero"
          className="rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Status
          </p>
          <p className="text-3xl font-semibold text-foreground">
            {upCount}/{totalCount} healthy
          </p>
        </div>
      )}

      {/* Page-level states */}
      {healthQuery.isLoading && <Loading label="Loading upstream health…" />}
      {healthQuery.isError && (
        <ErrorState
          title={
            healthQuery.error instanceof Error
              ? healthQuery.error.message
              : "Failed to load upstream health"
          }
        />
      )}

      {/* Data — a single untabbed view (see file header for why History was removed) */}
      {healthQuery.data && <UpstreamsTable data={healthQuery.data} />}
    </section>
  );
}
