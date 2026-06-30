"use client";

/**
 * AlertsPage — the dashboard Alerts surface (admin-only nav). Reads the tenant-visible
 * alert history from GET /admin/alerts (via the BFF catch-all) and renders it in a table.
 *
 * Visibility is enforced by the gateway: the response includes the tenant's own soft-budget
 * alerts plus platform system events (circuit-open, upstream-health, drift). This page is a
 * read-only viewer — the four states (loading / error / empty / success) are rendered
 * identically to every other dashboard surface.
 *
 * Auth guard: middleware.ts handles cookie-presence server-side. The admin-only nav link and
 * the gateway's owner/admin enforcement keep members out.
 */

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { AlertsTable, AlertsData } from "./AlertsTable";

export function AlertsPage() {
  const alertsQuery = useQuery<AlertsData>({
    queryKey: ["admin-alerts"],
    queryFn: () => bffGet<AlertsData>("/admin/alerts"),
  });

  return (
    <section
      aria-labelledby="alerts-heading"
      className="flex flex-col gap-6"
    >
      <PageHeader
        title="Alerts"
        titleId="alerts-heading"
        description="Soft-budget, circuit-breaker, upstream-health and drift events for your tenant."
      />

      {alertsQuery.isLoading ? (
        <Loading label="Loading alerts…" />
      ) : alertsQuery.isError ? (
        <ErrorState
          title={
            alertsQuery.error instanceof Error
              ? alertsQuery.error.message
              : "Failed to load alerts"
          }
        />
      ) : (
        <AlertsTable data={alertsQuery.data} />
      )}
    </section>
  );
}
