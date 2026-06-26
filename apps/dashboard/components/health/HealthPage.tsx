"use client";

/**
 * HealthPage — the dashboard Upstream Health surface (admin-only nav). Reads per-upstream
 * up/down status from GET /admin/health/upstreams (via the BFF catch-all) and renders it in a
 * table.
 *
 * Status is GLOBAL platform state derived by the gateway from durable health events — this page
 * is a read-only viewer. The four states (loading / error / empty / success) render identically
 * to every other dashboard surface. Only genuinely-monitored upstreams are shown; the backend
 * never fabricates a healthy row for an unpinged provider.
 *
 * Auth guard: middleware.ts handles cookie-presence server-side. The admin-only nav link and the
 * gateway's owner/admin enforcement keep members out.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { UpstreamsTable, UpstreamHealthData } from "./UpstreamsTable";

export function HealthPage() {
  const healthQuery = useQuery<UpstreamHealthData>({
    queryKey: ["admin-upstream-health"],
    queryFn: () => bffGet<UpstreamHealthData>("/admin/health/upstreams"),
  });

  return (
    <section
      aria-labelledby="health-heading"
      className="flex flex-col gap-6"
    >
      <h1
        id="health-heading"
        className="text-2xl font-semibold tracking-tight text-foreground"
      >
        Upstream Health
      </h1>

      {healthQuery.isLoading ? (
        <Loading label="Loading upstream health…" />
      ) : healthQuery.isError ? (
        <ErrorState
          title={
            healthQuery.error instanceof Error
              ? healthQuery.error.message
              : "Failed to load upstream health"
          }
        />
      ) : (
        <UpstreamsTable data={healthQuery.data} />
      )}
    </section>
  );
}
