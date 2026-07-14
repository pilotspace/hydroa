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
 * Auth guard: proxy.ts's route guard covers the initial navigation to /app/*; bff-
 * client.ts's own 401 handling covers a session that lapses while this page is
 * already mounted. The admin-only nav link and the gateway's owner/admin
 * enforcement keep members out.
 *
 * audit-remediation item 7: a client-side severity filter narrows alertsQuery.data.items
 * before it reaches AlertsTable (filtering, not fetching — GET /admin/alerts has no
 * severity query param since the backend has no severity field at all, see
 * lib/alert-severity.ts). Row drill-down lives inside AlertsTable itself.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState } from "@/components/ui";
import { PageHeader } from "@/components/ui/page-header";
import { AlertsTable, AlertsData } from "./AlertsTable";
import { ALERT_SEVERITIES, AlertSeverity, SEVERITY_LABELS, classifyAlertSeverity } from "@/lib/alert-severity";

export function AlertsPage() {
  const alertsQuery = useQuery<AlertsData>({
    queryKey: ["admin-alerts"],
    queryFn: () => bffGet<AlertsData>("/admin/alerts"),
  });
  const [severityFilter, setSeverityFilter] = useState<AlertSeverity | "all">("all");

  const filteredData = useMemo<AlertsData | undefined>(() => {
    if (!alertsQuery.data) return alertsQuery.data;
    if (severityFilter === "all") return alertsQuery.data;
    const items = alertsQuery.data.items.filter(
      (item) => classifyAlertSeverity(item.event_type) === severityFilter
    );
    return { ...alertsQuery.data, items, total: items.length };
  }, [alertsQuery.data, severityFilter]);

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
        <>
          <div className="flex flex-col gap-1 sm:w-64">
            <label htmlFor="alert-severity-filter" className="text-sm font-medium text-foreground">
              Severity
            </label>
            <select
              id="alert-severity-filter"
              value={severityFilter}
              onChange={(e) => setSeverityFilter(e.target.value as AlertSeverity | "all")}
              className="h-9 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="all">All</option>
              {ALERT_SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {SEVERITY_LABELS[severity]}
                </option>
              ))}
            </select>
          </div>
          <AlertsTable data={filteredData} />
        </>
      )}
    </section>
  );
}
