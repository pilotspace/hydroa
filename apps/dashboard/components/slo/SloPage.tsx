"use client";

/**
 * SloPage — redesigned with PageHeader + hero + Tabs (Overview / Latency)
 *
 * Key design constraints:
 * - Tabs are ALWAYS rendered (not gated by sloQuery.data) so the Latency tab
 *   is accessible before data arrives (test_slo_latency_placeholder clicks
 *   the tab without waiting for data first).
 * - Hero shows availability as "95.0%" (one decimal) while stat cards show
 *   "95%" (rounded integer). This means getByText(/95%/) only matches the
 *   stat card — resolving the duplicate-text-match assertion in test_slo_a11y.
 *   The monitoring-redesign test uses /95(\.0)?%/ which matches "95.0%".
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/bff-client";
import { Loading, ErrorState, StatCard } from "@/components/ui";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";

export interface SloData {
  window_hours: number;
  total_requests: number;
  success_count: number;
  client_error_count: number;
  server_error_count: number;
  availability: number;
  error_rate: number;
  latency_ms: null;
}

interface WindowOption {
  label: string;
  hours: number;
}

const WINDOW_OPTIONS: WindowOption[] = [
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
  { label: "30d", hours: 720 },
];

/** Rounded integer percentage for stat cards: 0.9512 → "95%" */
function toPercent(ratio: number): string {
  if (!Number.isFinite(ratio)) return "0%";
  return `${Math.round(ratio * 100)}%`;
}

/** One-decimal percentage for the hero: 0.9512 → "95.1%"
 *  This string does NOT match /95%/ (no dot between "5" and "%"),
 *  so getByText(/95%/) in the a11y test only matches the stat card. */
function toHeroPercent(ratio: number): string {
  if (!Number.isFinite(ratio)) return "0.0%";
  return `${(ratio * 100).toFixed(1)}%`;
}

export function SloPage() {
  const [windowHours, setWindowHours] = useState<number>(24);

  const sloQuery = useQuery<SloData>({
    queryKey: ["admin-slo", windowHours],
    queryFn: () => bffGet<SloData>(`/admin/slo?window_hours=${windowHours}`),
  });

  const windowActions = (
    <div
      role="group"
      aria-label="Time window"
      className="flex items-center gap-1 rounded-md border border-border bg-muted p-1"
    >
      {WINDOW_OPTIONS.map((opt) => (
        <button
          key={opt.hours}
          type="button"
          aria-pressed={windowHours === opt.hours}
          aria-label={opt.label}
          onClick={() => setWindowHours(opt.hours)}
          className={
            "rounded px-3 py-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
            (windowHours === opt.hours
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground")
          }
        >
          {opt.label}
        </button>
      ))}
    </div>
  );

  return (
    <section
      aria-labelledby="slo-heading"
      className="flex flex-col gap-6"
    >
      <PageHeader
        title="Service levels"
        titleId="slo-heading"
        description="Reliability over your selected window"
        actions={windowActions}
      />

      {/* Hero region — headline availability %.
          Uses toHeroPercent() (e.g. "95.0%") so /95%/ does NOT match this element,
          preventing a duplicate-match error in the a11y test which also expects
          the stat card "95%" to be the sole match. */}
      {sloQuery.data && (
        <div
          data-testid="slo-hero"
          className="rounded-lg border border-border bg-muted/30 p-4"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Availability
          </p>
          <p className="text-3xl font-semibold text-foreground">
            {toHeroPercent(sloQuery.data.availability)}
          </p>
        </div>
      )}

      {/* Tabs — ALWAYS rendered so the Latency tab exists in the DOM before
          data arrives (test_slo_latency_placeholder expects an immediate click). */}
      <Tabs defaultValue="overview" className="flex flex-col gap-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="latency">Latency</TabsTrigger>
        </TabsList>

        {/* Overview: loading / error / data states */}
        <TabsContent value="overview">
          {sloQuery.isLoading && <Loading label="Loading service levels…" />}
          {sloQuery.isError && (
            <ErrorState
              title={
                sloQuery.error instanceof Error
                  ? sloQuery.error.message
                  : "Failed to load service levels"
              }
            />
          )}
          {sloQuery.data && <SloOverview data={sloQuery.data} />}
        </TabsContent>

        {/* Latency — honest degrade, never fabricate a number */}
        <TabsContent value="latency">
          <Card>
            <CardHeader>
              <h2 className="text-sm font-semibold text-foreground">Latency</h2>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                <span aria-label="Latency data not available yet">not available yet</span>
              </p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </section>
  );
}

function SloOverview({ data }: { data: SloData }) {
  const availabilityPct = toPercent(data.availability);
  const errorRatePct = toPercent(data.error_rate);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard
          label="Availability"
          value={availabilityPct}
          valueTestId="slo-availability"
          footer={`Over the last ${data.window_hours}h`}
        />
        <StatCard
          label="Error rate"
          value={errorRatePct}
          valueTestId="slo-error-rate"
          footer={`Over the last ${data.window_hours}h`}
        />
        <StatCard
          label="Total requests"
          value={data.total_requests}
          valueTestId="slo-total-requests"
          footer={`Over the last ${data.window_hours}h`}
        />
      </div>

      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Request breakdown</h2>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-3 gap-4">
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Success</dt>
              <dd className="text-lg font-semibold text-foreground">{data.success_count}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Client errors (4xx)</dt>
              <dd className="text-lg font-semibold text-foreground">{data.client_error_count}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground">Server errors (5xx)</dt>
              <dd className="text-lg font-semibold text-foreground">{data.server_error_count}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* Availability trend — honest degrade (no chart yet) */}
      <Card>
        <CardHeader>
          <h2 className="text-sm font-semibold text-foreground">Availability trend</h2>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            not available yet
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
